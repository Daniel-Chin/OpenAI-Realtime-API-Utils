import openai.types.realtime as tp_rt

from ..shared import parse_client_event_param
from .shared import MetadataHandlerRosterManager

class TrackConfig:
    '''
    Client-side repr of session config.  
    '''

    roster_manager = MetadataHandlerRosterManager('TrackConfig')

    def __init__(self):
        self.session_config: tp_rt.RealtimeSessionCreateRequest | None = None

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                self.session_config = event.session
        return event, metadata

    @roster_manager.decorate
    def client_event_handler(
        self, event_param: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        event = parse_client_event_param(event_param)
        if isinstance(event, tp_rt.SessionUpdateEvent):
            self.session_config = None
        return event_param, metadata
