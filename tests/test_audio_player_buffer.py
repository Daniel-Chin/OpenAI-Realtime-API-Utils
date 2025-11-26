# Drafted by GPT-5
# pytest tests for FIFO well-orderedness only.

import random
import math
import pytest
from functools import lru_cache

@lru_cache(maxsize=1)
def config_info():
    from openai.types.realtime import realtime_audio_formats

    from openai_realtime_api_utils.audio_config import ConfigInfo, FormatInfo

    return ConfigInfo(FormatInfo(realtime_audio_formats.AudioPCM(
        type='audio/pcm',
        rate=24000, 
    )), 8)   # low n_samples_per_page for higher edge-case coverage

def bytes_like(b):
    # Ensure uniform type for concatenation (handles bytes | memoryview)
    return b if isinstance(b, bytes) else bytes(b)


def run_once(seed: int, total_len: int):
    from openai_realtime_api_utils.middlewares.audio_player import Buffer

    rng = random.Random(seed)
    buf = Buffer(config_info())

    # Generate random payload
    data = rng.randbytes(total_len)

    # Stream in random chunk sizes, and interleave pops when at least one page is available.
    i = 0
    bytes_appended = 0
    pages_popped = 0
    outputs: list[bytes | memoryview] = []

    # Helper: how many full pages are available given what we've appended/popped
    def pages_available() -> int:
        return (bytes_appended // config_info().n_bytes_per_page) - pages_popped

    while i < total_len or pages_popped < math.ceil(total_len / config_info().n_bytes_per_page):
        # Decide randomly to append or pop, but never pop if no pages are ready
        do_append = (
            i < total_len
            and (pages_available() == 0 or rng.random() < 0.7)
        )
        if do_append:
            # Random chunk size in [1, 64], bounded by remaining
            k = min(total_len - i, rng.randint(1, 64))
            chunk = data[i:i + k]
            buf.append(chunk)
            i += k
            bytes_appended += k
        else:
            # Pop exactly one full page
            outputs.append(buf.pop()[0])
            pages_popped += 1

    assembled = b''.join(bytes_like(x) for x in outputs)

    # FIFO well-orderedness: the concatenation of popped pages, when truncated to the
    # original length, must equal the original data byte-for-byte.
    assert assembled[:total_len] == data


@pytest.mark.parametrize('seed,total_len', [
    (0xA11CE, 1),
    (0xBEEF, 17),
    (0xC0FFEE, 257),
    (0xDEADBEEF & 0xFFFFFFFF, 4093),
    (123456789, 8191),
])
def test_fifo_well_ordered_randomized(seed, total_len):
    run_once(seed, total_len)


@pytest.mark.parametrize('seed', [101, 202, 303, 404, 505])
def test_fifo_well_ordered_many_lengths(seed):
    rng = random.Random(seed)
    # Run several randomized lengths without deliberately targeting edges
    for _ in range(20):
        total_len = rng.randint(1, 10_000)
        run_once(rng.randrange(1 << 30), total_len)

if __name__ == '__main__':
    pytest.main([__file__])
