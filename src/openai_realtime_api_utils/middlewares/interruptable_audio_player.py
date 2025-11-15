import pyaudio

from .interrupt import (
    Interrupt, AsyncRealtimeConnection, RealtimePlaybackTracker, 
    TrackConfig, TrackConversation,
)
from .audio_player import AudioPlayer

def interruptable_audio_player(
    connection: AsyncRealtimeConnection, 
    playback_tracker: RealtimePlaybackTracker, 
    track_config: TrackConfig,
    track_conversation: TrackConversation,
    pa: pyaudio.PyAudio, 
    n_samples_per_page: int = 2048, 
    output_device_index: int | None = None, 
):
    audio_player = AudioPlayer(
        pa, 
        n_samples_per_page, 
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
