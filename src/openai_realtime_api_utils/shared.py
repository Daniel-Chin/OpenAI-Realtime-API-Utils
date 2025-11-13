import copy
import logging
import typing as tp
from contextlib import contextmanager

import websockets
import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_server_event import ConversationItemRetrieved
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai._models import construct_type_unchecked

ServerEventHandler = tp.Callable[
    [tp_rt.RealtimeServerEvent, AsyncRealtimeConnection], 
    tp_rt.RealtimeServerEvent, 
]
ClientEventHandler = tp.Callable[
    [tp_rt.RealtimeClientEventParam, AsyncRealtimeConnection], 
    tp_rt.RealtimeClientEventParam, 
]

def parse_client_event_param(
    event_param: tp_rt.RealtimeClientEventParam, 
) -> tp_rt.RealtimeClientEvent:
    return tp.cast(
        tp_rt.RealtimeClientEvent, construct_type_unchecked(
            value=event_param, 
            type_=tp.cast(tp.Any, tp_rt.RealtimeClientEvent), 
        )
    )

@contextmanager
def hook_handlers(
    connection: AsyncRealtimeConnection, 
    serverEventHandlers: list[ServerEventHandler], 
    clientEventHandlers: list[ClientEventHandler], 
):
    async def keep_receiving():
        while True:
            try:
                event = await connection.recv()
            except websockets.exceptions.ConnectionClosedOK:
                print('WebSocket connection closed normally')
                return
            for sHandler in serverEventHandlers:
                event = sHandler(event, connection)
    
    async def send(event: tp_rt.RealtimeClientEventParam) -> None:
        for cHandler in clientEventHandlers:
            event = cHandler(event, connection)
        await connection.send(event)
    
    yield keep_receiving, send

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

def omitAudio(audio: str) -> str:
    return f'<omitted {len(audio)} bytes>'

def itemWithAudioOmitted(item: tp_rt.ConversationItem) -> tp_rt.ConversationItem:
    match item:
        case (
            tp_rt.RealtimeConversationItemAssistantMessage() | 
            tp_rt.RealtimeConversationItemUserMessage()
        ):
            it = copy.deepcopy(item)
            for c in it.content:
                if c.audio:
                    c.audio = omitAudio(c.audio)
            return it
        case _:
            return item

def strServerEventOmitAudio(event: tp_rt.RealtimeServerEvent) -> str:
    match event:
        case tp_rt.ResponseAudioDeltaEvent():
            e = event.model_copy()
            e.delta = omitAudio(e.delta)
        case ConversationItemRetrieved():
            e = event.model_copy()
            e.item = itemWithAudioOmitted(e.item)
        case _:
            e = event
    return str(e)

def strClientEventOmitAudio(eventParam: tp_rt.RealtimeClientEventParam) -> str:
    event = parse_client_event_param(eventParam)
    match event:
        case tp_rt.InputAudioBufferAppendEvent():
            eventParam_ = tp.cast(tp_rt.InputAudioBufferAppendEventParam, eventParam)
            eP = eventParam_.copy()
            eP['audio'] = omitAudio(eP['audio'])
        case _:
            eP = eventParam
    return str(eP)

def strItemOmitAudio(item: tp_rt.ConversationItem) -> str:
    return str(itemWithAudioOmitted(item))
