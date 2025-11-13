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

PART_TO_CONTENT_TYPE: dict[
    tp.Literal["text", "audio"], 
    tp.Literal["output_text", "output_audio"],
] = {
    'text': 'output_text', 
    'audio': 'output_audio', 
}

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

def pages_of(
    signal: bytes, n_bytes_per_page: int, 
):
    for start in range(0, len(signal), n_bytes_per_page):
        yield signal[start: start + n_bytes_per_page]

def get_logger(log_pathname: str) -> logging.Logger:
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

def omit_audio(audio: str) -> str:
    return f'<omitted {len(audio)} bytes>'

def item_with_audio_omitted(item: tp_rt.ConversationItem) -> tp_rt.ConversationItem:
    match item:
        case (
            tp_rt.RealtimeConversationItemAssistantMessage() | 
            tp_rt.RealtimeConversationItemUserMessage()
        ):
            it = copy.deepcopy(item)
            for c in it.content:
                if c.audio:
                    c.audio = omit_audio(c.audio)
            return it
        case _:
            return item

def str_server_event_omit_audio(event: tp_rt.RealtimeServerEvent) -> str:
    match event:
        case tp_rt.ResponseAudioDeltaEvent():
            e = event.model_copy(update=dict(
                delta=omit_audio(event.delta)
            ))
        case ConversationItemRetrieved():
            e = event.model_copy(update=dict(
                item=item_with_audio_omitted(event.item)
            ))
        case _:
            e = event
    return str(e)

def str_client_event_omit_audio(eventParam: tp_rt.RealtimeClientEventParam) -> str:
    event = parse_client_event_param(eventParam)
    match event:
        case tp_rt.InputAudioBufferAppendEvent():
            eventParam_ = tp.cast(tp_rt.InputAudioBufferAppendEventParam, eventParam)
            eP = eventParam_.copy()
            eP['audio'] = omit_audio(eP['audio'])
        case _:
            eP = eventParam
    return str(eP)

def str_item_omit_audio(item: tp_rt.ConversationItem) -> str:
    return str(item_with_audio_omitted(item))

def item_from_param(
    item_param: tp_rt.ConversationItemParam, /, 
) -> tp_rt.ConversationItem:
    return construct_type_unchecked(
        value=item_param, 
        type_=tp.cast(tp.Any, tp_rt.ConversationItem),
    )
