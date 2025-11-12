import asyncio
import base64
import wave

import openai
from openai.resources.realtime import AsyncRealtime
import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_conversation_item_user_message

from openai_realtime_api_utils.shared import strEventOmitAudio, keep_handling

async def main():
    with wave.open('./test.wav', 'rb') as w:
        pcm_data = w.readframes(w.getnframes())

    def handler(event: tp_rt.RealtimeServerEvent) -> None:
        print(strEventOmitAudio(event))

    a_oa = openai.AsyncOpenAI()
    a_r = AsyncRealtime(a_oa)
    async with a_r.connect(
        model='gpt-realtime-mini',
    ) as conn:
        asyncio.create_task(keep_handling(conn, [handler]))

        await conn.send(tp_rt.SessionUpdateEventParam(
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
        await conn.send(tp_rt.InputAudioBufferAppendEventParam(
            type='input_audio_buffer.append',
            audio=base64.encodebytes(pcm_data).decode('utf-8'),
        ))
        await conn.send(tp_rt.InputAudioBufferCommitEventParam(
            type='input_audio_buffer.commit',
        ))
        await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
