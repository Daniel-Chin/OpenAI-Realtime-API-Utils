import asyncio
from functools import cached_property
import typing as tp
import warnings

import openai.types.realtime as tp_rt
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from agents.realtime import RealtimePlaybackTracker

from .shared import MetadataHandlerRosterManager
from . import TrackConfig, TrackConversation, AudioPlayer

class Interrupt:
    '''
    - Whenever:
        - the user starts talking while an assistant speech is playing, 
        - or, a new assistant speech arrives while the user is talking,  
    - Do:
        - stop the local audio player
        - mark the truncation in local conversation
        - send to server
            - response.cancel
            - conversation.item.truncate
            - conversation.item.retrieve
                - just to get the updated item with truncated audio
    - Unless the item has already been interrupted.
        - avoids repeated interrupts when server events are delayed.
    '''

    roster_manager = MetadataHandlerRosterManager('Interrupt')

    def __init__(
        self, 
        connection: AsyncRealtimeConnection, 
        playback_tracker: RealtimePlaybackTracker, 
        track_config: TrackConfig,
        track_conversation: TrackConversation,
        on_interrupt: tp.Callable[[], None],
    ):
        self.connection = connection
        self.playback_tracker = playback_tracker
        self.track_config = track_config
        self.track_conversation = track_conversation
        self.on_interrupt = on_interrupt

        self.is_user_talking = False
        self._send_with_handlers: tp.Callable[
            [tp_rt.RealtimeClientEventParam], tp.Awaitable[None], 
        ] | None = None
        self.already_interrupted: set[str] = set()
    
    def register_send_with_handlers(
        self, 
        send_with_handlers: tp.Callable[
            [tp_rt.RealtimeClientEventParam], tp.Awaitable[None], 
        ], 
    ) -> None:
        '''
        Give me the `send` yielded by `hook_handlers`.  
        '''
        self._send_with_handlers = send_with_handlers
    
    @cached_property
    def send_with_handlers(self) -> tp.Callable[
        [tp_rt.RealtimeClientEventParam], tp.Awaitable[None], 
    ]:
        if self._send_with_handlers is None:
            warnings.warn(
                'send_with_handlers not registered yet, using connection.send directly',
                RuntimeWarning,
            )
            return self.connection.send
        return self._send_with_handlers
    
    def set_is_user_talking(self, value: bool, /) -> None:
        '''
        Only call this in client VAD.  
        '''
        config = self.track_config.session_config
        if config is not None:
            assert config.audio is not None
            assert config.audio.input is not None
            assert config.audio.input.turn_detection is None, config.audio.input.turn_detection
        self.is_user_talking = value
        if value:
            self._on_speech_started()
    
    def _on_speech_started(self) -> None:
        state = self.playback_tracker.get_state()
        match state:
            case {
                'current_item_id': current_item_id, 
                'current_item_content_index': current_item_content_index, 
                'elapsed_ms': elapsed_ms, 
            }:
                if current_item_id is None or current_item_content_index is None:
                    # nothing is playing
                    return
                self._start_interrupt(
                    current_item_id,
                    current_item_content_index,
                    elapsed_ms or 0.0,
                )
            case _:
                raise TypeError(f'Unexpected state: {state}')
    
    def _start_interrupt(
        self, 
        current_item_id: str, 
        current_item_content_index: int, 
        elapsed_ms: float, 
    ) -> None:
        if current_item_id in self.already_interrupted:
            return
        self.already_interrupted.add(current_item_id)
        asyncio.create_task(self._interrupt(
            current_item_id,
            current_item_content_index,
            elapsed_ms,
        ))
    
    async def _interrupt(
        self, 
        current_item_id: str, 
        current_item_content_index: int, 
        elapsed_ms: float, 
    ) -> None:
        self.on_interrupt() # pause audio playback
        self.playback_tracker.on_interrupted()  # in fact is the responsibility of the audio player, but let's make sure
        cell = self.track_conversation.conversation_group.get_cell_from_id(
            current_item_id, 
        )
        cell.audio_truncate = (
            current_item_content_index, 
            round(elapsed_ms), 
        )
        await self.send_with_handlers(
            tp_rt.ResponseCancelEventParam(
                type='response.cancel',
            ),
        )
        await self.send_with_handlers(
            tp_rt.ConversationItemTruncateEventParam(
                type='conversation.item.truncate',
                item_id=current_item_id,
                content_index=current_item_content_index,
                audio_end_ms=round(elapsed_ms),
            ),
        )
        await self.send_with_handlers(
            tp_rt.ConversationItemRetrieveEventParam(
                type='conversation.item.retrieve',
                item_id=current_item_id,
            ),
        )

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent | None, dict]:
        match event:
            case tp_rt.InputAudioBufferSpeechStartedEvent():
                self.is_user_talking = True
                self._on_speech_started()
                out_event = event
            case tp_rt.InputAudioBufferSpeechStoppedEvent():
                self.is_user_talking = False
                out_event = event
            case tp_rt.ResponseAudioDeltaEvent():
                assert not self.roster_manager.is_in_roster(
                    AudioPlayer.roster_manager.handler_name, metadata, 
                ), 'AudioPlayer must handle after Interrupt.'
                if self.is_user_talking:
                    state = self.playback_tracker.get_state()
                    if state['current_item_id'] == event.item_id:
                        elapsed = state['elapsed_ms'] or 0.0
                    else:
                        elapsed = 0.0
                    assert elapsed == 0.0, elapsed  # this new speech must have not started yet
                    self._start_interrupt(
                        event.item_id,
                        event.content_index,
                        elapsed,
                    )
                    out_event = None
                else:
                    out_event = event
            case _:
                out_event = event
        return out_event, metadata
