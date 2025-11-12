import asyncio

import openai.types.realtime as tp_rt
from openai.types.realtime import realtime_conversation_item_user_message

from openai_realtime_api_utils import (
    Interface, BasicConfig, InterfaceException, 
)

async def main():
    def handler(event: tp_rt.RealtimeServerEvent | InterfaceException) -> None:
        match event:
            case InterfaceException():
                print(f'<Error> {event}')
            case _:
                print(f'<Server> {event}')

    async with Interface().context(BasicConfig(), handler) as interface:
        await interface.send(tp_rt.ConversationItemCreateEvent(
            type='conversation.item.create',
            item=tp_rt.RealtimeConversationItemUserMessage(
                type='message',
                role='user',
                content=[realtime_conversation_item_user_message.Content(
                    type='input_text',
                    text='What is three plus four? Be brief.',
                )],
                status='completed',
            ),
        ))
        await interface.send(tp_rt.ResponseCreateEvent(
            type='response.create',
        ))
        await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
