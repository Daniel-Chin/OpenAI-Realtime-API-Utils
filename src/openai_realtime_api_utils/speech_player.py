import asyncio
import typing as tp
from contextlib import contextmanager
from dataclasses import dataclass

from .shared import *

appendSpeechType = tp.Callable[[ItemID, int, bytes], None]
setEndOfSpeechType = tp.Callable[[ItemID, int], None]

@dataclass()
class Speech:
    item_id: ItemID
    content_index: int
    audio: tp.List[bytes]
    playhead: float # sec
    more_to_come: bool

@contextmanager
def SpeechPlayer(
    page_len: int, sr: int, n_bytes_per_sample: int, 
    outputAudio: tp.Callable[[bytes, int], tp.Awaitable[None]], 
    onInterrupted: tp.Callable[[ItemID, int, int], tp.Awaitable[None]],
):
    '''
    An audio player that knows where the current playhead position is.  
    Anything written to `outputAudio` is assumed to be instantaneously played.  
    In other words, you want small latency between `outputAudio` and the user hearing the audio.  
    '''
    speeches: tp.List[Speech] = []
    roster: tp.Dict[ItemID, Speech] = {}
    doorBell = asyncio.Event()

    def appendSpeech(
        item_id: ItemID, content_index: int, audio: bytes, 
    ):
        try:
            speech = roster[item_id]
        except KeyError:
            speech = Speech(
                item_id, content_index, [], 0.0, more_to_come=True, 
            )
            speeches.append(speech)
            roster[item_id] = speech
        speech.audio.append(audio)
        doorBell.set()
    
    def setEndOfSpeech(
        item_id: ItemID, content_index: int, 
    ):
        speech = roster[item_id]
        assert speech.content_index == content_index
        speech.more_to_come = False
    
    async def interrupt():
        tasks = [onInterrupted(
            speech.item_id, speech.content_index, 
            int(speech.playhead * 1000), 
        ) for speech in speeches]
        speeches.clear()
        roster.clear()
        await asyncio.gather(*tasks)
    
    async def worker():
        n_bytes_per_page = page_len * n_bytes_per_sample
        sec_per_byte = 1 / sr / n_bytes_per_sample
        try:
            while True:
                while True:
                    try:
                        speech = speeches[0]
                    except IndexError:
                        break
                    try:
                        segment = speech.audio.pop(0)
                    except IndexError:
                        if speech.more_to_come:
                            break
                        speeches.pop(0)
                        del roster[speech.item_id]
                        continue
                    if len(segment) > n_bytes_per_page:
                        speech.audio = [
                            *PagesOf(segment, n_bytes_per_page), 
                            *speech.audio, 
                        ]
                        continue
                    await outputAudio(
                        segment, len(segment) // n_bytes_per_sample, 
                    )
                    speech.playhead += len(segment) * sec_per_byte
                doorBell.clear()
                await doorBell.wait()
        except asyncio.CancelledError:
            pass
    
    yield worker, appendSpeech, setEndOfSpeech, interrupt
