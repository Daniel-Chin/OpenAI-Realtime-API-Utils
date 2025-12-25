import openai.types.realtime as tp_rt

from ..shared import parse_client_event_param
from .shared import MetadataHandlerRosterManager

class TrackConfig:
    '''
    Client-side repr of session config.  
    During the window where the client wants to update the session config 
    but the server hasn't acknowledged it, the interpretation of incoming events 
    can be ambiguous. During that window, this class does the following:  
    - `session_config` is `None`.  
      - To communicate this ambiguity downstream.  
    - `audio_format_input` and `audio_format_output` track the last known audio formats.  
      - Changing audio formats mid session is unrealistic.  
    '''

    roster_manager = MetadataHandlerRosterManager('TrackConfig')

    def __init__(self):
        self.session_config: tp_rt.RealtimeSessionCreateRequest | None = None
        self.audio_format_input : tp_rt.RealtimeAudioFormats | None = None
        self.audio_format_output: tp_rt.RealtimeAudioFormats | None = None

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        match event:
            case tp_rt.SessionUpdatedEvent():
                assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
                self.session_config = event.session
                self._maybe_update_audio_formats(event.session)
        return event, metadata

    @roster_manager.decorate
    def client_event_handler(
        self, event_param: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        event = parse_client_event_param(event_param)
        if isinstance(event, tp_rt.SessionUpdateEvent):
            self.session_config = None
            assert isinstance(event.session, tp_rt.RealtimeSessionCreateRequest)
            self._maybe_update_audio_formats(event.session)
        return event_param, metadata

    def _maybe_update_audio_formats(self, config: tp_rt.RealtimeSessionCreateRequest):
        if config.audio is None:
            return
        if config.audio.input is not None:
            self.audio_format_input  = config.audio.input.format
        if config.audio.output is not None:
            self.audio_format_output = config.audio.output.format
