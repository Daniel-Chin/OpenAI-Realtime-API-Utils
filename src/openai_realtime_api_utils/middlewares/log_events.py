import typing as tp
import logging

import openai.types.realtime as tp_rt

from ..shared import (
    str_server_event_omit_audio, str_client_event_omit_audio, 
)
from .shared import MetadataHandlerRosterManager

class LogEvents:
    roster_manager = MetadataHandlerRosterManager('LogEvents')

    def __init__(
        self, 
        filter_server: tp.Optional[tp.Callable[[
            tp_rt.RealtimeServerEvent
        ], bool]] = None, 
        filter_client: tp.Optional[tp.Callable[[
            tp_rt.RealtimeClientEventParam
        ], bool]] = None,
        str_server_event: tp.Callable[[
            tp_rt.RealtimeServerEvent
        ], str] = str_server_event_omit_audio,
        str_client_event: tp.Callable[[
            tp_rt.RealtimeClientEventParam
        ], str] = str_client_event_omit_audio,
    ):
        self.filter_server = filter_server
        self.filter_client = filter_client
        self.str_server_event = str_server_event
        self.str_client_event = str_client_event

        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)
    
    @roster_manager.decorate
    def server_event_handler(
        self, 
        event: tp_rt.RealtimeServerEvent, 
        metadata: dict, _, 
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        if self.filter_server is None or self.filter_server(event):
            match event:
                case tp_rt.RealtimeErrorEvent():
                    if event.error.code == 'response_cancel_not_active':
                        f = self.logger.info
                    else:
                        f = self.logger.warning
                case _:
                    f = self.logger.debug
            f(
                f'Server: {self.str_server_event(event)}\n'
                f'event {metadata = }', 
            )
        return event, metadata
    
    @roster_manager.decorate
    def client_event_handler(
        self, 
        eventParam: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _, 
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        if self.filter_client is None or self.filter_client(eventParam):
            self.logger.debug(
                f'Client: {self.str_client_event(eventParam)}\n'
                f'eventParam {metadata = }', 
            )
        return eventParam, metadata

def error_only(
    event: tp_rt.RealtimeServerEvent,
) -> bool:
    match event:
        case tp_rt.RealtimeErrorEvent():
            return True
        case _:
            return False

def unexpected_error_only(
    event: tp_rt.RealtimeServerEvent,
) -> bool:
    match event:
        case tp_rt.RealtimeErrorEvent():
            if event.error.code == 'response_cancel_not_active':
                return False
            else:
                return True
        case _:
            return False
