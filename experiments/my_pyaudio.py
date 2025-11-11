from __future__ import annotations

import typing as tp
import asyncio
from contextlib import contextmanager
import time
import math

import pyaudio

@contextmanager
def pyAudio():
    pa = pyaudio.PyAudio()
    try:
        yield pa
    finally:
        pa.terminate()

@contextmanager
def streamContext(stream: pyaudio.Stream):
    stream.start_stream()
    try:
        yield stream
    finally:
        stream.stop_stream()
        stream.close()

@contextmanager
def SmallBuffer(
    stream: pyaudio.Stream, buffer_n_samples: int, sr: int, 
    verbose: bool = True, 
):
    '''
    Limit latency by pretending there's a small audio buffer.  
    Writing blocks if the conceptual buffer is full.  
    '''
    assert not stream.is_active
    buffer_time = buffer_n_samples / sr
    if verbose:
        print(f'SmallBuffer maximum latency = {math.ceil(buffer_time * 1000)} ms.')
    drain_point = time.time()

    async def write(data: bytes, n_samples: int):
        nonlocal drain_point
        stream.write(data)
        drain_point = max(drain_point, time.time())
        drain_point += n_samples / sr
        to_wait = drain_point - buffer_time - time.time()
        if to_wait > 0:
            await asyncio.sleep(to_wait)
    
    with streamContext(stream):
        yield write

@contextmanager
def PushToTalk(
    stream: pyaudio.Stream,
    onAudioIn: tp.Callable[[bytes, int], tp.Awaitable[None]], 
    onRelease: tp.Callable[[], tp.Awaitable[None]], 
    onInterrupt: tp.Callable[[], tp.Awaitable[None]],
    page_len: int, 
):
    lock = asyncio.Lock()
    is_pressed = False

    async def relayer():
        try:
            page = await asyncio.to_thread(stream.read, page_len)
            async with lock:
                if is_pressed:
                    await onAudioIn(page, page_len)
        except asyncio.CancelledError:
            pass

    async def inputter():
        nonlocal is_pressed
        print('Press Enter to toggle push-to-talk.')
        while True:
            try:
                await asyncio.to_thread(input)
                async with lock:
                    is_pressed = not is_pressed
                if is_pressed:
                    print('Now recording...')
                    await onInterrupt()
                else:
                    print('Recording finished.')
                    await onRelease()
            except asyncio.CancelledError:
                break
    
    with streamContext(stream):
        yield asyncio.gather(relayer(), inputter())
