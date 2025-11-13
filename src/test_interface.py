import asyncio
import base64
import wave

import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_conversation_item_user_message

from openai_realtime_api_utils import (
    Interface, BasicConfig, InterfaceException, 
)
from openai_realtime_api_utils.shared import strServerEventOmitAudio

async def main():
    with wave.open('./test.wav', 'rb') as w:
        pcm_data = w.readframes(w.getnframes())

    def handler(event: tp_rt.RealtimeServerEvent | InterfaceException) -> None:
        match event:
            case InterfaceException():
                print(f'<Error> {event}')
            case _:
                print(f'<Server> {strServerEventOmitAudio(event)}')

    async with Interface().context(BasicConfig(), [handler]) as interface:
        await interface.send(tp_rt.SessionUpdateEvent(
            type='session.update',
            session=tp_rt.RealtimeSessionCreateRequest(
                type='realtime',
                audio=tp_rt.RealtimeAudioConfig(
                    input=tp_rt.RealtimeAudioConfigInput(
                        turn_detection=None,
                    ),
                ),
            ),
        ))
        await asyncio.sleep(1.5)
        return
        # await interface.send(tp_rt.ConversationItemCreateEvent(
        #     type='conversation.item.create',
        #     item=tp_rt.RealtimeConversationItemUserMessage(
        #         type='message',
        #         role='user',
        #         content=[realtime_conversation_item_user_message.Content(
        #             type='input_text',
        #             text='What is three plus four? Be brief.',
        #         )],
        #         status='completed',
        #     ),
        # ))
        await interface.send(tp_rt.InputAudioBufferAppendEvent(
            type='input_audio_buffer.append',
            audio=base64.encodebytes(pcm_data).decode('utf-8'),
        ))
        await interface.send(tp_rt.InputAudioBufferCommitEvent(
            type='input_audio_buffer.commit',
        ))
        # await interface.send(tp_rt.ResponseCreateEvent(
        #     type='response.create',
        # ))
        await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
