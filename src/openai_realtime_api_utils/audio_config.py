from __future__ import annotations

from functools import cached_property
from dataclasses import dataclass

import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_audio_formats

N_CHANNELS = 1  # OpenAI decided on mono without documenting it

class UnderSpecified(Exception):
    pass

class OverSpecified(Exception):
    pass

@dataclass(frozen=True)
class ConfigSpecification:
    '''
    If None, use whatever the OpenAI API server proposes.  
    '''
    format: tp_rt.RealtimeAudioFormats | None
    n_samples_per_page: int | None
    page_latency_ms: float | tuple[float, float] | None

    def __post_init__(self):
        if self.n_samples_per_page is None and self.page_latency_ms is None:
            raise UnderSpecified('Page (buffer) length not specified.')
        if self.n_samples_per_page is not None and isinstance(self.page_latency_ms, (float, int)):
            raise OverSpecified(
                'If you specify n_samples_per_page, page_latency must be either `None` (auto) or `(min_, max_)` (assertion).', 
            )

    def resolve(
        self, 
        server_proposed: tp_rt.RealtimeAudioFormats | None, 
        verbose: bool,
    ) -> ConfigInfo:
        format = self.format or server_proposed
        if format is None:
            raise UnderSpecified('Audio format not specified by client or server.')
        format_info = FormatInfo(format=format)
        if self.n_samples_per_page is not None:
            info = ConfigInfo(
                n_samples_per_page=self.n_samples_per_page, 
                format_info=format_info, 
            )
            if verbose:
                print(f'{self.n_samples_per_page = } means latency = {round(info.ms_per_page)} ms')
            match self.page_latency_ms:
                case (min_, max_):
                    assert min_ <= info.ms_per_page <= max_
                    return info
                case None:
                    return info
                case _:
                    raise RuntimeError('Unreachable')
        else:
            match self.page_latency_ms:
                case (min_, max_):
                    target_ms = (min_ + max_) / 2.0
                case float() | int():
                    target_ms = self.page_latency_ms
                case _:
                    raise RuntimeError('Unreachable')
            n_samples_per_page = round(
                target_ms / 1000.0 * format_info.sample_rate
            )
            if verbose:
                print(f'Target latency = {target_ms} ms means {n_samples_per_page = }')
            return ConfigInfo(
                n_samples_per_page=n_samples_per_page, 
                format_info=format_info, 
            )

@dataclass(frozen=True)
class FormatInfo:
    '''
    Use pure functions? Clean code.  
    Use dataclass? Just for cached_property. Faster than lru_cache.  
    '''
    format: tp_rt.RealtimeAudioFormats

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
    def bytes_per_second(self) -> int:
        return (
            self.sample_rate 
            * N_CHANNELS 
            * self.n_bytes_per_sample
        )
    
    @cached_property
    def ms_per_byte(self) -> float:
        return 1000.0 / self.bytes_per_second

@dataclass(frozen=True)
class ConfigInfo:
    format_info: FormatInfo
    n_samples_per_page: int

    @cached_property
    def n_bytes_per_page(self) -> int:
        return N_CHANNELS * self.format_info.n_bytes_per_sample * self.n_samples_per_page
    
    @cached_property
    def silence_page(self) -> bytes:
        return self.format_info.silence_sample * self.n_samples_per_page
    
    @cached_property
    def ms_per_page(self) -> float:
        return self.n_samples_per_page / self.format_info.sample_rate * 1000.0

EXAMPLE_SPECIFICATION = ConfigSpecification(
    format=None,
    n_samples_per_page=None,
    page_latency_ms=(80.0, 90.0),
)
