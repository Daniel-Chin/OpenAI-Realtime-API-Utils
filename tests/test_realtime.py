# ruff: noqa: F401, F841

import asyncio
import base64
import wave

import openai
from openai.resources.realtime import AsyncRealtime
import openai.types.realtime as tp_rt

from openai_realtime_api_utils.middlewares import (
    GiveClientEventId, 
)
from openai_realtime_api_utils.shared import (
    hook_handlers,
    str_server_event_omit_audio, str_client_event_omit_audio, 
)

async def main():
    with wave.open('./test.wav', 'rb') as w:
        pcm_data = w.readframes(w.getnframes())

    def sHandler(event: tp_rt.RealtimeServerEvent, _) -> tp_rt.RealtimeServerEvent:
        print(str_server_event_omit_audio(event))
        return event
    def cHandler(eventParam: tp_rt.RealtimeClientEventParam, _) -> tp_rt.RealtimeClientEventParam:
        print(str_client_event_omit_audio(eventParam))
        return eventParam

    a_oa = openai.AsyncOpenAI()
    a_r = AsyncRealtime(a_oa)
    async with a_r.connect(
        model='gpt-realtime-mini',
    ) as conn:
        with hook_handlers(conn, [sHandler], [
            GiveClientEventId().handleClientEvent, cHandler, 
        ]) as (keep_receiving, send):
            asyncio.create_task(keep_receiving())

            await send(tp_rt.SessionUpdateEventParam(
                type='session.update',
                session=tp_rt.RealtimeSessionCreateRequestParam(
                    type='realtime',
                    audio=tp_rt.RealtimeAudioConfigParam(
                        input=tp_rt.RealtimeAudioConfigInputParam(
                            turn_detection=None,
                        ),
                    ),
                ),
            ))
            # await send(tp_rt.InputAudioBufferAppendEventParam(
            #     type='input_audio_buffer.append',
            #     audio=base64.encodebytes(pcm_data).decode('utf-8'),
            # ))
            # await send(tp_rt.InputAudioBufferCommitEventParam(
            #     type='input_audio_buffer.commit',
            # ))

            await send(tp_rt.ConversationItemCreateEventParam(
                type='conversation.item.create',
                item=tp_rt.RealtimeConversationItemUserMessageParam(
                    type='message',
                    role='user',
                    content=[tp_rt.realtime_conversation_item_user_message_param.Content(
                        type='input_text',
                        text='What is three plus four? Be brief.',
                    )],
                ),
            ))
            await send(tp_rt.ResponseCreateEventParam(
                type='response.create',
                response=tp_rt.RealtimeResponseCreateParamsParam(
                    conversation='none',
                    metadata=dict(laser='69'),
                    output_modalities=['text'],
                ),
            ))

            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
