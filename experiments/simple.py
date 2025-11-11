import os
import asyncio
import base64
from contextlib import ExitStack

import dotenv
import pyaudio

import openai_realtime_api
from openai_realtime_api.shared import *
from openai_realtime_api import defaults
from openai_realtime_api.speech_player import SpeechPlayer, appendSpeechType, setEndOfSpeechType
from openai_realtime_api.tests.my_pyaudio import pyAudio, SmallBuffer, PushToTalk
from shared import EventID, ItemID, ResponseID

SR = 24000
PAGE_LEN = 2048
print(f'audio page: {round(PAGE_LEN / SR * 1000)} ms')

# >>> should inter-agree
OPENAI_AUDIO_FORMAT = 'pcm16'
PA_FORMAT = pyaudio.paInt16
n_bytes_per_sample = 2
# <<<

class MyClient(openai_realtime_api.Client):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.display = Display()

        self.appendSpeech: appendSpeechType | None = None
        self.setEndOfSpeech: setEndOfSpeechType | None = None
    
    def logSnapshot(self):
        with open('conversation.md', 'w') as f:
            for i, (item_id, cell) in enumerate(self.server_conversation):
                item = self.items[item_id]
                print(i, item, file=f)
                print(self.reprCell(cell), file=f)
    
    async def keepLoggingSnapshots(self):
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            self.logSnapshot()

    def onError(
        self, event_id: EventID, 
        error: OpenAIError,
    ):
        super().onError(event_id, error, do_warn=True)
        input('Press Enter to continue...')
        # blocks all coroutines. Good!

    def onConversationItemInputAudioTranscriptionCompleted(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, transcript: str, 
    ):
        super().onConversationItemInputAudioTranscriptionCompleted(
            event_id, item_id, content_index, transcript,
        )
        self.display.print('Transcribed:', transcript)
    
    def onResponseAudioDelta(
        self, event_id: str, 
        response_id: str, item_id: str, output_index: int, 
        content_index: int, delta: str, 
    ):
        assert self.appendSpeech is not None
        self.appendSpeech(
            item_id, content_index, base64.b64decode(delta), 
        )
    
    def onResponseAudioDone(
        self, event_id: EventID, 
        response_id: EventID, item_id: EventID, 
        output_index: int, content_index: int, 
    ):
        super().onResponseAudioDone(
            event_id, response_id, item_id, output_index, 
            content_index, 
        )
        assert self.setEndOfSpeech is not None
        self.setEndOfSpeech(item_id, content_index)

    async def appendAudio(
        self, 
        data: bytes, # paInt16
        n_samples: int,
    ):
        base64_encoded = base64.b64encode(data).decode()
        await self.inputAudioBufferAppend(
            base64_encoded, 
        )

class Display:
    def __init__(self):
        self.is_streaming = False
        self.buf: tp.List[str] = []
    
    def print(self, *a, **kw):
        if self.is_streaming:
            print()
            self.is_streaming = False
        print(*a, **kw)
    
    def delta(self, x: str, /):
        self.buf.append(x)
        if self.is_streaming:
            print(x, end='', flush=True)
        else:
            print(*self.buf, sep='', end='', flush=True)
            self.is_streaming = True
    
    def finishStream(self):
        self.buf.clear()
        if self.is_streaming:
            print()
            self.is_streaming = False

async def main():
    assert dotenv.load_dotenv('openai_api.env')
    openai_api_key = os.getenv('OPENAI_API_KEY')
    assert openai_api_key is not None
    async with MyClient.Context(openai_api_key, SessionConfig(
        ResponseConfig(
            (Modality.AUDIO, Modality.TEXT),
            defaults.INSTRUCTIONS, 
            'alloy', OPENAI_AUDIO_FORMAT, (), 'auto', 0.8, 'inf', 
        ), 
        OPENAI_AUDIO_FORMAT, 'whisper-1', None, 
    )) as client:
        del openai_api_key

        with ExitStack() as stack:
            pa = stack.enter_context(pyAudio())
            outStream = pa.open(
                rate=SR, channels=1, format=PA_FORMAT, 
                output=True, frames_per_buffer=PAGE_LEN, 
                start=False, 
            )
            writeAudio = stack.enter_context(
                SmallBuffer(outStream, PAGE_LEN * 2, SR), 
            )
            inStream = pa.open(
                rate=SR, channels=1, format=PA_FORMAT, 
                input=True, frames_per_buffer=PAGE_LEN, 
                start=False, 
            )
            (
                speechPlayer, client.appendSpeech, 
                client.setEndOfSpeech, interrupt, 
            ) = stack.enter_context(SpeechPlayer(
                PAGE_LEN, SR, n_bytes_per_sample, writeAudio, 
                client.conversationItemTruncate, 
            ))
            pushToTalk = stack.enter_context(PushToTalk(
                    inStream, client.appendAudio, 
                    client.inputAudioBufferCommit, interrupt, 
                    PAGE_LEN, 
            )) 
            await asyncio.gather(
                client.interface.receiveLoop(), 
                client.keepLoggingSnapshots(), 
                pushToTalk, 
                speechPlayer(), 
            )

if __name__ == '__main__':
    asyncio.run(main())
