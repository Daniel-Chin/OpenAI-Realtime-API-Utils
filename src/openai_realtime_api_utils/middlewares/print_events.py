import typing as tp

import openai.types.realtime as tp_rt

from ..shared import (
    str_server_event_omit_audio, str_client_event_omit_audio, 
)
from .shared import MetadataHandlerRosterManager

class PrintEvents:
    roster_manager = MetadataHandlerRosterManager('PrintEvents')

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
    
    @roster_manager.decorate
    def server_event_handler(
        self, 
        event: tp_rt.RealtimeServerEvent, 
        metadata: dict, _, 
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        if self.filter_server is None or self.filter_server(event):
            print()
            print(f'Server: {self.str_server_event(event)}')
            print(f'event {metadata = }')
        return event, metadata
    
    @roster_manager.decorate
    def client_event_handler(
        self, 
        eventParam: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _, 
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        if self.filter_client is None or self.filter_client(eventParam):
            print(f'Client: {self.str_client_event(eventParam)}')
            print(f'event {metadata = }')
            print()
        return eventParam, metadata
