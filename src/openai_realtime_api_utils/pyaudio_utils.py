from contextlib import contextmanager

import pyaudio

@contextmanager
def py_audio():
    pa = pyaudio.PyAudio()
    try:
        yield pa
    finally:
        pa.terminate()

@contextmanager
def stream_context(stream: pyaudio.Stream):
    stream.start_stream()
    try:
        yield stream
    finally:
        stream.stop_stream()
        stream.close()
