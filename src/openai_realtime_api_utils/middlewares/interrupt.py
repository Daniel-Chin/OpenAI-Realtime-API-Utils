import asyncio
from functools import cached_property
import typing as tp
import warnings

import openai.types.realtime as tp_rt
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from agents.realtime import RealtimePlaybackTracker
import websockets

from .shared import MetadataHandlerRosterManager
from .track_config import TrackConfig
from .track_conversation import TrackConversation
from ..audio_config import FormatInfo as AudioFormatInfo

class Interrupt:
    '''
    - Whenever:
        - the user starts talking while an assistant speech is playing, 
        - or, a new assistant speech arrives while the user is talking,  
    - Do:
        - stop the local audio player
        - mark the truncation in local conversation
            - Both audio and transcript in ConversationGroup.Cell.  
        - send to server
            - response.cancel
            - conversation.item.truncate
            - DO NOT conversation.item.retrieve
                - The retrieved "transcript" field is empty. What a letdown.  
                - What would you gain from retrieve?  
    - Unless the item has already been interrupted.
        - avoids repeated interrupts when server events are delayed.
    '''

    IS_DURING_USER_SPEECH = 'is_during_user_speech'
    
    roster_manager = MetadataHandlerRosterManager('Interrupt')

    def __init__(
        self, 
        connection: AsyncRealtimeConnection, 
        track_config: TrackConfig,
        track_conversation: TrackConversation,
        playback_tracker: RealtimePlaybackTracker, 
        on_interrupt: tp.Callable[[], None],
        interruptee_type: tp.Type, 
    ):
        self.connection = connection
        self.track_config = track_config
        self.track_conversation = track_conversation
        self.playback_tracker = playback_tracker
        self.on_interrupt = on_interrupt
        self.interruptee_type = interruptee_type

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
        assert self.track_config.audio_format_output is not None
        speech_total_ms = cell.audio_total_bytes * AudioFormatInfo(
            self.track_config.audio_format_output
        ).ms_per_byte
        
        progress_ratio = elapsed_ms / speech_total_ms
        item = self.track_conversation.all_items[current_item_id]
        assert isinstance(item, tp_rt.RealtimeConversationItemAssistantMessage)
        content = item.content[current_item_content_index]
        if content.transcript is not None:
            cell.truncated_transcript = content.transcript[
                :round(len(content.transcript) * progress_ratio)
            ]   # approximately
        try:
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
        except websockets.ConnectionClosedOK:
            pass

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent | None, dict]:
        # >>> the interruptee handler must run after Interrupt handler
        assert isinstance(
            interruptee_roster_manager := self.interruptee_type.roster_manager, 
            MetadataHandlerRosterManager, 
        )
        assert not self.roster_manager.is_in_roster(
            interruptee_roster_manager.handler_name, metadata, 
        )
        # <<<
        match event:
            case tp_rt.InputAudioBufferSpeechStartedEvent():
                self.is_user_talking = True
                self._on_speech_started()
            case tp_rt.InputAudioBufferSpeechStoppedEvent():
                self.is_user_talking = False
            case tp_rt.ResponseAudioDeltaEvent():
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
                    metadata[__class__.IS_DURING_USER_SPEECH] = True
        return event, metadata
