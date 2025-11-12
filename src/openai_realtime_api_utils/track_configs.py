import openai.types.realtime as tp_rt

class TrackConfigs:
    '''
    Client-side repr of session config.  
    '''
    def __init__(self):
        self.session_config: tp_rt.RealtimeSessionCreateRequest | None = None

    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, 
    ) -> tp_rt.RealtimeServerEvent:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                self.session_config = event.session
        return event
