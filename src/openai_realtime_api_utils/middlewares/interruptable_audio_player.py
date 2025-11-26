import pyaudio

from .interrupt import (
    Interrupt, AsyncRealtimeConnection, RealtimePlaybackTracker, 
    TrackConfig, TrackConversation,
)
from .audio_player import AudioPlayer
from ..audio_config import ConfigSpecification, EXAMPLE_SPECIFICATION

def interruptable_audio_player(
    connection: AsyncRealtimeConnection, 
    playback_tracker: RealtimePlaybackTracker, 
    track_config: TrackConfig,
    track_conversation: TrackConversation,
    pa: pyaudio.PyAudio, 
    config_specification: ConfigSpecification = EXAMPLE_SPECIFICATION,
    output_device_index: int | None = None, 
):
    audio_player = AudioPlayer(
        pa, 
        config_specification, 
        output_device_index, 
        playback_tracker,
        skip_delta_metadata_keyword=Interrupt.IS_DURING_USER_SPEECH,
    )
    interrupt = Interrupt(
        connection, 
        track_config,
        track_conversation,
        playback_tracker, 
        on_interrupt=audio_player.interrupt,
        interruptee_type=AudioPlayer, 
    )

    return (
        audio_player, interrupt, 
        (
            interrupt.server_event_handler,
            audio_player.server_event_handler,
        ), # order matters
        interrupt.register_send_with_handlers, 
    )
