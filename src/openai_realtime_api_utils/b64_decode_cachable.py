from base64 import b64decode

import openai.types.realtime as tp_rt

CACHE_KEY = 'delta_b64_decoded_cache'

def b64_decode_cachable(event: tp_rt.ResponseAudioDeltaEvent, metadata: dict) -> bytes:
    """Decode base64 data, caching the result in metadata to avoid redundant work."""
    if CACHE_KEY not in metadata:
        metadata[CACHE_KEY] = b64decode(event.delta)
    return metadata[CACHE_KEY]
