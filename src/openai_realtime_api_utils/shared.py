import copy
import logging
import typing as tp

import websockets
import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_server_event import ConversationItemRetrieved
from openai.resources.realtime.realtime import AsyncRealtimeConnection

EventHandler = tp.Callable[[tp_rt.RealtimeServerEvent], None]

async def keep_handling(
    connection: AsyncRealtimeConnection, eventHandlers: list[EventHandler], 
):
    while True:
        try:
            event = await connection.recv()
        except websockets.exceptions.ConnectionClosedOK:
            print('WebSocket connection closed normally')
            return
        for handler in eventHandlers:
            handler(event)

def PagesOf(
    signal: bytes, n_bytes_per_page: int, 
):
    for start in range(0, len(signal), n_bytes_per_page):
        yield signal[start: start + n_bytes_per_page]

def getLogger(log_pathname: str) -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_pathname)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s]: %(message)s',
        '%Y-%m-%d %H:%M:%S',
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def itemWithAudioOmitted(item: tp_rt.ConversationItem) -> tp_rt.ConversationItem:
    match item:
        case (
            tp_rt.RealtimeConversationItemAssistantMessage() | 
            tp_rt.RealtimeConversationItemUserMessage()
        ):
            it = copy.deepcopy(item)
            for c in it.content:
                if c.audio and len(c.audio) > 10:
                    c.audio = '<omitted>'
            return it
        case _:
            return item

def strEventOmitAudio(event: tp_rt.RealtimeServerEvent) -> str:
    match event:
        case tp_rt.ResponseAudioDeltaEvent():
            e = event.model_copy()
            e.delta = '<omitted>'
        case ConversationItemRetrieved():
            e = event.model_copy()
            e.item = itemWithAudioOmitted(e.item)
        case _:
            e = event
    return str(e)

def strItemOmitAudio(item: tp_rt.ConversationItem) -> str:
    return str(itemWithAudioOmitted(item))
