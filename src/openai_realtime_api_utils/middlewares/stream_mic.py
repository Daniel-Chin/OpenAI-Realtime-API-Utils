import typing as tp
from base64 import b64encode
import itertools
import asyncio
from contextlib import contextmanager

import pyaudio
import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_audio_formats
import websockets

from .shared import MetadataHandlerRosterManager
from ..audio_config import N_CHANNELS, ConfigSpecification, ConfigInfo, UnderSpecified
from ..niceness import NicenessManager, ThreadPriority

PAYLOAD_SIZE_LIMIT = 15 * 1024 * 1024  # 15 MiB
PAYLOAD_SIZE_THRESHOLD = round(PAYLOAD_SIZE_LIMIT * 0.9)

class StreamMic:
    '''
    Stream host sound input to realtime API.  
    Assumes server / semantic VAD.  
    If `audio_config_specification` is underspecified, waits for server 
    to provide audio format and then opens stream. Otherwise, opens stream 
    immediately.  
    '''

    roster_manager = MetadataHandlerRosterManager('StreamMic')

    def __init__(
        self, 
        pa: pyaudio.PyAudio, 
        audio_config_specification: ConfigSpecification,
        input_device_index: int | None = None, 
    ):
        self.pa = pa
        self.audio_config_specification = audio_config_specification
        self.input_device_index = input_device_index
        self.config_info: ConfigInfo | None = None
        self.stream: pyaudio.Stream | None = None
        self._send_with_handlers: tp.Callable[
            [tp_rt.RealtimeClientEventParam], tp.Awaitable[None], 
        ] | None = None
        self.event_id_iter = itertools.count()
        self.asyncio_loop = asyncio.get_event_loop()
        self.buffer = asyncio.Queue[bytes]()
        self.niceness_manager = NicenessManager()

        self.maybe_open_stream()
    
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
            input=True,
            frames_per_buffer=config_info.n_samples_per_page,
            input_device_index=self.input_device_index,
            stream_callback=self.on_audio_in,   # type: ignore
        )
        self.stream.start_stream()
        print(__class__.__name__ + ': stream opened.')
        asyncio.create_task(self.worker(), name='StreamMic_worker')
    
    def _set_audio_config(self, from_server: tp_rt.RealtimeAudioFormats | None) -> ConfigInfo:
        if self.config_info is None:
            self.config_info = self.audio_config_specification.resolve(from_server, 'input')
        else:
            assert self.config_info.format_info.format == from_server, (
                'Changing audio format mid-stream is unsupported.'
                f'Current: {self.config_info.format_info.format}, '
                f'new: {from_server}.'
            )
        return self.config_info
    
    def on_audio_in(self, in_data: bytes, frame_count, time_info, status):
        self.niceness_manager.maybe_set(ThreadPriority.high)
        self.asyncio_loop.call_soon_threadsafe( # preserves order
            self.buffer.put_nowait, in_data,
        )
        return None, pyaudio.paContinue
    
    async def send_audio(self, data: bytes) -> None:
        assert self._send_with_handlers is not None
        event = tp_rt.InputAudioBufferAppendEventParam(
            type = 'input_audio_buffer.append',
            audio = b64encode(data).decode('ascii'),
            event_id='client-a-' + str(next(self.event_id_iter)),
        )
        await self._send_with_handlers(event)
    
    async def worker(self) -> None:
        _buf = []
        _buf_size = 0

        def append(data: bytes) -> None:
            nonlocal _buf_size
            _buf.append(data)
            _buf_size += len(data)
        
        def harvest() -> bytes:
            nonlocal _buf_size
            collated = b''.join(_buf)
            _buf.clear()
            _buf_size = 0
            return collated
        
        while self.stream is not None:
            append(await self.buffer.get())
            while _buf_size < PAYLOAD_SIZE_THRESHOLD:
                try:
                    append(self.buffer.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await self.send_audio(harvest())
            except websockets.ConnectionClosedOK:
                assert self.stream is None
                return

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                assert event.session.audio is not None
                assert event.session.audio.input is not None
                assert event.session.audio.input.format is not None
                self._set_audio_config(event.session.audio.input.format)
                self.maybe_open_stream()
        return event, metadata

    def register_send_with_handlers(
        self, 
        send_with_handlers: tp.Callable[
            [tp_rt.RealtimeClientEventParam], tp.Awaitable[None], 
        ], 
    ) -> None:
        '''
        Give me the `send` yielded by `hook_handlers`.  
        '''
        self._send_with_handlers = send_with_handlers

    @contextmanager
    def context(self):
        try:
            yield self
        finally:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            self.buffer.put_nowait(b'')  # unblock worker
