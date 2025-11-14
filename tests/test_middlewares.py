# ruff: noqa: F401, F841

import os
import sys
import asyncio

import openai
from openai.resources.realtime import AsyncRealtime
import openai.types.realtime as tp_rt

from openai_realtime_api_utils import hook_handlers, middlewares

LOG_STDOUT = os.getenv('LOG_STDOUT')
if LOG_STDOUT:
    f = open('./tests/logs/test_middlewares.log', 'w', buffering=1)  # line-buffered
    sys.stdout = f
    # sys.stderr = f

async def main():
    a_oa = openai.AsyncOpenAI()
    a_r = AsyncRealtime(a_oa)
    track_config = middlewares.TrackConfig()
    track_conversation = middlewares.TrackConversation()

    def f(e, _):
        print('<config>')
        if track_config.session_config is None:
            print('Unavailable, or invalidated.')
        else:
            print(track_config.session_config.model_dump())
        print('</config>')
        track_conversation.print_conversation()
        print()
        return e

    async with a_r.connect(
        model='gpt-realtime-mini',
    ) as connection:
        with hook_handlers(
            connection, 
            serverEventHandlers = [
                track_config.server_event_handler,
                track_conversation.server_event_handler,
                middlewares.PrintEvents().server_event_handler,
                f, 
            ], 
            clientEventHandlers = [
                middlewares.GiveClientEventId().client_event_handler, 
                track_config.client_event_handler,
                track_conversation.client_event_handler,
                middlewares.PrintEvents().client_event_handler,
            ],
        ) as (keep_receiving, send):
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
                    # conversation='none',
                    metadata=dict(laser='69'),
                    output_modalities=['text'],
                ),
            ))

            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
