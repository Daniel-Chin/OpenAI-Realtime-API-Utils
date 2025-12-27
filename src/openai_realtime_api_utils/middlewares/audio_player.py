from __future__ import annotations

import typing as tp
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import asyncio
import threading

import pyaudio
import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_audio_formats
from agents.realtime import RealtimePlaybackTracker
from uuid import UUID, uuid4

from .shared import MetadataHandlerRosterManager, AsyncOnSpeechEndHandler
from ..audio_config import N_CHANNELS, ConfigSpecification, ConfigInfo, UnderSpecified
from ..niceness import NicenessManager, ThreadPriority
from ..b64_decode_cachable import b64_decode_cachable

class AudioPlayer:
    '''
    Plays assistant audio to local output device.  
    Syncs its audio playhead with a PlaybackTracker, if supplied.  
    If `audio_config_specification` is underspecified, waits for server 
    to provide audio format and then opens stream. Otherwise, opens stream 
    immediately.  
    Emits an event on finishing playing an assistant speech via `on_speech_end_handlers`.  
    '''

    roster_manager = MetadataHandlerRosterManager('AudioPlayer')

    def __init__(
        self, pa: pyaudio.PyAudio, 
        audio_config_specification: ConfigSpecification,
        output_device_index: int | None = None, 
        playback_tracker: RealtimePlaybackTracker | None = None,
        skip_delta_metadata_keyword: str | None = None,
        on_speech_end_handlers: dict[UUID, AsyncOnSpeechEndHandler] | None = None,
    ):
        self.pa = pa
        self.audio_config_specification = audio_config_specification
        self.output_device_index = output_device_index
        self.playback_tracker_thread_safe = None if (
            playback_tracker is None
        ) else RealtimePlaybackTrackerThreadSafe(playback_tracker, self)
        self.skip_delta_metadata_keyword = skip_delta_metadata_keyword
        self.on_speech_end_handlers = on_speech_end_handlers or {}
        self.config_info: ConfigInfo | None = None
        self.stream: pyaudio.Stream | None = None
        self.speeches: deque[Speech] = deque()
        self.lock = threading.Lock()
        self.pyaudio_niceness_manager = NicenessManager()
        self.maybe_open_stream()    # Why this soon? To fail fast if config unsupported by host.  
    
    def maybe_open_stream(self) -> None:
        if self.stream is not None:
            return
        if (config_info := self.config_info) is None:
            try:
                config_info = self._set_audio_config(None)
            except UnderSpecified:
                return
        assert isinstance(
            config_info.format_info.format, 
            realtime_audio_formats.AudioPCM,
        ), 'todo: implement transcoding to support other audio formats.'
        self.stream = self.pa.open(
            format=pyaudio.paInt16,  # OpenAI MAYBE decided on int16 without documenting it
            channels=N_CHANNELS,
            rate=config_info.format_info.sample_rate,
            output=True,
            frames_per_buffer=config_info.n_samples_per_page,
            output_device_index=self.output_device_index,
            stream_callback=self.on_audio_out,
        )
        self.stream.start_stream()
        return
    
    def _set_audio_config(self, from_server: tp_rt.RealtimeAudioFormats | None) -> ConfigInfo:
        if self.config_info is None:
            self.config_info = self.audio_config_specification.resolve(from_server, 'output')
        else:
            assert self.config_info.format_info.format == from_server, (
                'Changing audio format mid-stream is unsupported.'
                f'Current: {self.config_info.format_info.format}, '
                f'new: {from_server}.'
            )
        return self.config_info
    
    def on_audio_out(self, in_data, frame_count, time_info, status):
        self.pyaudio_niceness_manager.maybe_set(ThreadPriority.high)
        with self.lock:
            if not self.speeches:
                return (
                    self.config_info.silence_page, # type: ignore
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
            self.on_speech_end_handlers.clear()

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
                self._set_audio_config(event.session.audio.output.format)
                self.maybe_open_stream()
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
                    try:
                        buffer = self.get_speech(
                            event.item_id, event.content_index, 
                        ).buffer
                    except KeyError:
                        pass    # item already interrupted
                    else:
                        s = b64_decode_cachable(event, metadata)
                        with self.lock:
                            buffer.append(s)
            case tp_rt.ResponseContentPartAddedEvent():
                assert self.config_info is not None, (
                    'Looks like a speech event arrived before session config.',
                )
                if event.part.type == 'audio':
                    speech = Speech(
                        item_id=event.item_id,
                        content_index=event.content_index,
                        buffer=Buffer(self.config_info),
                    )
                    with self.lock:
                        self.speeches.append(speech)
            case tp_rt.ResponseContentPartDoneEvent():
                if event.part.type == 'audio':
                    try:
                        speech = self.get_speech(
                            event.item_id, event.content_index, 
                        )
                    except KeyError:
                        pass    # item already interrupted
                    else:
                        speech.has_more_to_come = False
        return event, metadata
    
    def interrupt(self):
        self.speeches.clear()
        if self.playback_tracker_thread_safe is not None:
            self.playback_tracker_thread_safe.inner.on_interrupted()
    
    def register_on_speech_end_handler(
        self, handler: AsyncOnSpeechEndHandler,
    ) -> tp.Callable[[], None]:
        uuid = uuid4()
        self.on_speech_end_handlers[uuid] = handler
        def unregister():
            self.on_speech_end_handlers.pop(uuid, None)
        return unregister

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

    def __init__(self, config_info: ConfigInfo):
        self.config_info = config_info

        self.dq: deque[memoryview | bytes] = deque()
        self.tail: bytes | None = None
    
    def pop(self) -> tuple[bytes | memoryview, int]:
        n_bytes_per_page = self.config_info.n_bytes_per_page
        try:
            return self.dq.popleft(), n_bytes_per_page
        except IndexError:
            if self.tail is None:
                return self.config_info.silence_page, 0
            result = self.tail + self.config_info.silence_page[
                len(self.tail):
            ]
            n_content_bytes = len(self.tail)
            self.tail = None
            return result, n_content_bytes
    
    def append(self, data: bytes):
        n_bytes_per_page = self.config_info.n_bytes_per_page
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
    has_more_to_come: bool = True   # if interrupted, unchanged.  

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
        assert self.parent.config_info is not None
        finished_speeches = list[Speech]()
        with self.parent.lock:
            while self.parent.speeches and self.parent.speeches[0].is_mission_accomplished():
                finished_speeches.append(self.parent.speeches.popleft())
            self.inner.on_play_ms(
                speech.item_id, 
                speech.content_index, 
                n_content_bytes * self.parent.config_info.format_info.ms_per_byte,
            )
        for finished_speech in finished_speeches:
            for handler in self.parent.on_speech_end_handlers.values():
                coro = handler(finished_speech.item_id, finished_speech.content_index)
                asyncio.create_task(coro)
    
    def update_soon_threadsafe(self, speech: Speech, n_content_bytes: int) -> None:
        self.asyncio_loop.call_soon_threadsafe(
            self.update, speech, n_content_bytes, 
        )
