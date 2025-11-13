import openai.types.realtime as tp_rt

from ..shared import parse_client_event_param

class TrackConfig:
    '''
    Client-side repr of session config.  
    '''
    def __init__(self):
        self.session_config: tp_rt.RealtimeSessionCreateRequest | None = None

    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, _, 
    ) -> tp_rt.RealtimeServerEvent:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                self.session_config = event.session
        return event

    def client_event_handler(
        self, event_param: tp_rt.RealtimeClientEventParam, _, 
    ) -> tp_rt.RealtimeClientEventParam:
        event = parse_client_event_param(event_param)
        if isinstance(event, tp_rt.SessionUpdateEvent):
            self.session_config = None
        return event_param
