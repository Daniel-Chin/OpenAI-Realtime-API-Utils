from __future__ import annotations

from functools import cached_property
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from base64 import b64decode
import asyncio
import threading

import pyaudio
import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_audio_formats
from agents.realtime import RealtimePlaybackTracker

from .shared import MetadataHandlerRosterManager
from ..niceness import NicenessManager, ThreadPriority

N_CHANNELS = 1  # OpenAI decided on mono without documenting it

@dataclass(frozen=True)
class FormatInfo:
    '''
    Use pure functions? Clean code.  
    Use dataclass? Just for cached_property. Faster than lru_cache.  
    '''
    format: tp_rt.RealtimeAudioFormats
    n_samples_per_page: int

    @cached_property
    def sample_rate(self) -> int:
        match self.format:
            case realtime_audio_formats.AudioPCM(rate=rate):
                return rate or 24000
            case realtime_audio_formats.AudioPCMA:
                return 8000
            case realtime_audio_formats.AudioPCMU:
                return 8000
            case _:
                raise ValueError(f'Unsupported audio format: {self.format}')
    
    @cached_property
    def n_bytes_per_sample(self) -> int:
        match self.format:
            case realtime_audio_formats.AudioPCM():
                return 2    # OpenAI decided on 16-bit without documenting it
            case realtime_audio_formats.AudioPCMA():
                return 1
            case realtime_audio_formats.AudioPCMU():
                return 1
            case _:
                raise ValueError(f'Unsupported audio format: {self.format}')
    
    @cached_property
    def silence_sample(self) -> bytes:
        match self.format:
            case realtime_audio_formats.AudioPCM():
                return b'\x00\x00'
            case _:
                # 0xD5 for a-law and 0xFF for u-law, but who knows about bit inversion?
                raise ValueError(f'Unsupported audio format: {self.format}')
    
    @cached_property
    def n_bytes_per_page(self) -> int:
        return N_CHANNELS * self.n_bytes_per_sample * self.n_samples_per_page
    
    @cached_property
    def silence_page(self) -> bytes:
        return self.silence_sample * self.n_samples_per_page
    
    @cached_property
    def bytes_per_second(self) -> int:
        return (
            self.sample_rate 
            * N_CHANNELS 
            * self.n_bytes_per_sample
        )
    
    @cached_property
    def ms_per_byte(self) -> float:
        return 1000.0 / self.bytes_per_second
    
    @cached_property
    def ms_per_page(self) -> float:
        return self.n_samples_per_page / self.sample_rate * 1000.0

class Buffer:
    '''
    State invariants:  
    - len(x) == n_bytes_per_page for any x in self.dq.  
    - self.tail is None or len(self.tail) < n_bytes_per_page.  

    Method behaviors:  
    - `append` and `pop` forms a queue-like orderedness on the bytes.  
    - `pop` returns exactly one page, padding with silence if necessary.  
    - `append` can take arbitrary length bytes.  

    Optimization rationale:  
    - Favor memoryview. Avoid copying.  
    '''

    def __init__(self, format_info: FormatInfo):
        self.format_info = format_info

        self.dq: deque[memoryview | bytes] = deque()
        self.tail: bytes | None = None
    
    def pop(self) -> tuple[bytes | memoryview, int]:
        n_bytes_per_page = self.format_info.n_bytes_per_page
        try:
            return self.dq.popleft(), n_bytes_per_page
        except IndexError:
            if self.tail is None:
                return self.format_info.silence_page, 0
            result = self.tail + self.format_info.silence_page[
                len(self.tail):
            ]
            n_content_bytes = len(self.tail)
            self.tail = None
            return result, n_content_bytes
    
    def append(self, data: bytes):
        n_bytes_per_page = self.format_info.n_bytes_per_page
        mv = memoryview(data)
        if self.tail is not None:
            tail_short_by = (
                n_bytes_per_page - len(self.tail)
            )
            self.tail += mv[:tail_short_by]
            if len(self.tail) < n_bytes_per_page:
                return
            self.dq.append(self.tail)
            self.tail = None
            mv = mv[tail_short_by:]
        while len(mv):
            segment, mv = (
                mv[:n_bytes_per_page ], 
                mv[ n_bytes_per_page:], 
            )
            if len(segment) == n_bytes_per_page:
                self.dq.append(segment)
            else:
                self.tail = bytes(segment)
                return
    
    def is_empty(self) -> bool:
        return len(self.dq) == 0 and self.tail is None

@dataclass
class Speech:
    item_id: str
    content_index: int
    buffer: Buffer
    has_more_to_come: bool = True

    def is_mission_accomplished(self) -> bool:
        return not self.has_more_to_come and self.buffer.is_empty()

class RealtimePlaybackTrackerThreadSafe:
    def __init__(
        self, inner: RealtimePlaybackTracker, /, 
        parent: AudioPlayer, 
    ):
        self.inner = inner
        self.parent = parent

        self.asyncio_loop = asyncio.get_event_loop()
    
    def update(self, speech: Speech, n_content_bytes: int) -> None:
        # May run after all is destroyed.  
        assert self.parent.format_info is not None
        with self.parent.lock:
            while self.parent.speeches and self.parent.speeches[0].is_mission_accomplished():
                self.parent.speeches.popleft()
            self.inner.on_play_ms(
                speech.item_id, 
                speech.content_index, 
                n_content_bytes * self.parent.format_info.ms_per_byte,
            )
    
    def update_soon_threadsafe(self, speech: Speech, n_content_bytes: int) -> None:
        self.asyncio_loop.call_soon_threadsafe(
            self.update, speech, n_content_bytes, 
        )

class AudioPlayer:
    roster_manager = MetadataHandlerRosterManager('AudioPlayer')

    def __init__(
        self, pa: pyaudio.PyAudio, 
        n_samples_per_page: int = 2048, 
        output_device_index: int | None = None, 
        playback_tracker: RealtimePlaybackTracker | None = None,
        skip_delta_metadata_keyword: str | None = None,
    ):
        self.pa = pa
        self.n_samples_per_page = n_samples_per_page
        self.output_device_index = output_device_index
        self.playback_tracker_thread_safe = None if (
            playback_tracker is None
        ) else RealtimePlaybackTrackerThreadSafe(playback_tracker, self)
        self.skip_delta_metadata_keyword = skip_delta_metadata_keyword
        self.format_info: FormatInfo | None = None
        self.speeches: deque[Speech] = deque()
        self.stream: pyaudio.Stream | None = None
        self.lock = threading.Lock()
        self.pyaudio_niceness_manager = NicenessManager()
    
    def set_format(self, format: tp_rt.RealtimeAudioFormats) -> None:
        if self.format_info is None:
            self.format_info = FormatInfo(format, self.n_samples_per_page)
        else:
            assert self.format_info.format == format, 'Changing audio format mid-stream is unsupported.'
    
    def on_audio_out(self, in_data, frame_count, time_info, status):
        self.pyaudio_niceness_manager.maybe_set(ThreadPriority.high)
        with self.lock:
            if not self.speeches:
                return (
                    self.format_info.silence_page, # type: ignore
                    pyaudio.paContinue, 
                )
            speech = self.speeches[0]
            data, n_content_bytes = speech.buffer.pop()
            if self.playback_tracker_thread_safe is not None:
                self.playback_tracker_thread_safe.update_soon_threadsafe(
                    speech, n_content_bytes,
                )
        return (
            bytes(data), # what a letdown! "argument 1 must be read-only bytes-like object, not memoryview"
            pyaudio.paContinue, 
        )
    
    @contextmanager
    def context(self):
        try:
            yield self
        finally:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None

    def get_speech(
        self, item_id: str, content_index: int,
    ) -> Speech:
        for speech in self.speeches:
            if (
                speech.item_id == item_id and 
                speech.content_index == content_index
            ):
                return speech
        raise KeyError(f'No speech found for item_id={item_id}, item_content_index={content_index}')
    
    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                assert event.session.audio is not None
                assert event.session.audio.output is not None
                assert event.session.audio.output.format is not None
                self.set_format(event.session.audio.output.format)
                assert self.format_info is not None
                print(f'{self.n_samples_per_page = } means latency = {round(self.format_info.ms_per_page)} ms')
                if self.stream is None:
                    assert isinstance(
                        self.format_info.format, 
                        realtime_audio_formats.AudioPCM,
                    ), 'todo: implement transcoding to support other audio formats.'
                    self.stream = self.pa.open(
                        format=pyaudio.paInt16,  # OpenAI MAYBE decided on int16 without documenting it
                        channels=N_CHANNELS,
                        rate=self.format_info.sample_rate,
                        output=True,
                        frames_per_buffer=self.n_samples_per_page,
                        output_device_index=self.output_device_index,
                        stream_callback=self.on_audio_out,
                    )
                    self.stream.start_stream()
            case tp_rt.ResponseAudioDeltaEvent():
                '''
                We assume single stream.  
                - Assumption 1: assistant speech generation is auto-regressive 
                even across items.  
                  - caveat: OOB speech response in parallel, but then 
                  what's that for?  
                - Assumption 2: websocket preserves event order.  
                  - valid.  
                '''
                if not metadata.get(self.skip_delta_metadata_keyword, False):
                    buffer = self.get_speech(
                        event.item_id, event.content_index, 
                    ).buffer
                    s = b64decode(event.delta)
                    with self.lock:
                        buffer.append(s)
            case tp_rt.ResponseContentPartAddedEvent():
                assert self.format_info is not None, (
                    'Looks like a speech event arrived before session config.',
                )
                if event.part.type == 'audio':
                    speech = Speech(
                        item_id=event.item_id,
                        content_index=event.content_index,
                        buffer=Buffer(self.format_info),
                    )
                    with self.lock:
                        self.speeches.append(speech)
            case tp_rt.ResponseContentPartDoneEvent():
                if event.part.type == 'audio':
                    speech = self.get_speech(
                        event.item_id, event.content_index, 
                    )
                    speech.has_more_to_come = False
        return event, metadata
    
    def interrupt(self):
        self.speeches.clear()
        if self.playback_tracker_thread_safe is not None:
            self.playback_tracker_thread_safe.inner.on_interrupted()
