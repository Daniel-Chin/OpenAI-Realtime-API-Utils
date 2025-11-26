from __future__ import annotations

from datetime import datetime
import typing as tp
from copy import deepcopy

import uuid
import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_server_event import ConversationItemRetrieved

from ..shared import (
    str_client_event_omit_audio, str_server_event_omit_audio, 
    str_item_omit_audio, parse_client_event_param, 
    item_from_param, PART_TO_CONTENT_TYPE, 
)
from .shared import MetadataHandlerRosterManager
from ..conversation_group import ConversationGroup

class TrackConversation:
    '''
    Client-side repr of the conversation.  
    - Does not consider:  
      - user image message.  
    - Does not keep:  
      - assistant audio;  
      - user input audio;  
      - UNLESS retrieved via ConversationItemRetrievedEvent.  
        - That API is explicitly for retrieving full audio anyways.  
      - response.output  
        - Otherwise, dual storage of conversation items.  
        - Still, fields of response other than output are kept.  
    - Locally synced by watching conversation.item.create:  
      - tool output.  
      - user text message.  
      - "faked" assistant message or tool call.  
    - Fast synced via delta:  
      - user input audio transcription;  
      - assistant text;  
      - assistant audio transcript.  
    - Slowly synced, disregarding delta:  
      - assistant tool call.  
    '''

    roster_manager = MetadataHandlerRosterManager('TrackConversation')

    class Impatience:
        def __init__(self, parent: TrackConversation):
            self.parent = parent
            self.locally_synced_awaiting_server_confirmation: set[str] = set()
            self.response_added_awaiting_conversation_insertion: dict[
                str, # item_id
                str, # response_id
            ] = {}
        
        def handle(
            self, 
            event_info: (
                tp_rt.ConversationItemAdded | 
                tp_rt.ResponseOutputItemAddedEvent | 
                tp_rt.ConversationItemCreateEventParam
            ),
        ) -> None:
            match event_info:
                case {'type': 'conversation.item.create'} as event_param:
                    item_param = event_param['item']
                    item_id = item_param.get('id', None)
                    previous_item_id = event_param['previous_item_id'] # type: ignore
                    assert item_id is not None
                    assert item_id not in self.locally_synced_awaiting_server_confirmation
                    self.locally_synced_awaiting_server_confirmation.add(item_id)
                    assert item_id not in self.parent.all_items
                    self.parent.all_items[item_id] = item_from_param(item_param)
                    self.parent.conversation_group.insert_after(
                        ConversationGroup.Cell(item_id=item_id), 
                        previous_item_id, 
                    )
                    self.parent.conversation_group.touch(
                        item_id, event_param.get('event_id', None), 
                    )
                case tp_rt.ResponseOutputItemAddedEvent() as event:
                    item_id = event.item.id
                    assert item_id is not None
                    assert item_id not in self.parent.all_items
                    response = self.parent.responses[event.response_id]
                    if response.conversation_id is None:
                        self.parent.all_items[item_id] = event.item
                        self.parent.conversation_group.safe_add_oob(
                            ConversationGroup.Cell(
                                item_id=item_id,
                                response_id=event.response_id,
                            )
                        )
                        self.parent.conversation_group.touch(item_id, event.event_id)
                    else:
                        assert item_id not in self.response_added_awaiting_conversation_insertion
                        self.response_added_awaiting_conversation_insertion[item_id] = event.response_id
                        assert item_id not in self.parent.all_items
                        self.parent.all_items[item_id] = event.item
                case tp_rt.ConversationItemAdded() as event:
                    item_id = event.item.id
                    assert item_id is not None
                    try:
                        self.locally_synced_awaiting_server_confirmation.remove(item_id)
                    except KeyError:
                        is_locally_synced = False
                    else:
                        is_locally_synced = True
                    try:
                        response_id = self.response_added_awaiting_conversation_insertion.pop(item_id)
                    except KeyError:
                        is_added_by_response = False
                        response_id = None
                    else:
                        is_added_by_response = True
                    assert not (is_locally_synced and is_added_by_response)
                    if is_locally_synced:
                        assert item_id in self.parent.all_items
                        old_item = self.parent.all_items[item_id]
                        old_item.status = event.item.status  # type: ignore
                        assert old_item == event.item, (
                            'I just thought the ConversationItemAdded after the ConversationItemCreateEvent would have identical item.',
                            old_item, event.item, 
                        )
                        self.parent.conversation_group.move(
                            item_id, event.previous_item_id, 
                        )
                        self.parent.conversation_group.touch(item_id, event.event_id)
                    elif is_added_by_response:
                        assert item_id in self.parent.all_items
                        dangling_item = self.parent.all_items[item_id]
                        assert dangling_item == event.item, (
                            'I just thought the ConversationItemAdded after the ResponseOutputItemAddedEvent would have identical item.', 
                            dangling_item, event.item,
                        )
                        assert response_id is not None
                        self.parent.conversation_group.insert_after(
                            ConversationGroup.Cell(
                                item_id=item_id,
                                response_id=response_id,
                            ),
                            event.previous_item_id, 
                        )
                        self.parent.conversation_group.touch(item_id, event.event_id)
                    else:
                        assert item_id not in self.parent.all_items
                        self.parent.all_items[item_id] = event.item
                        self.parent.conversation_group.insert_after(
                            ConversationGroup.Cell(item_id=item_id), 
                            event.previous_item_id, 
                        )
                        self.parent.conversation_group.touch(item_id, event.event_id)

    def __init__(self):
        self.conversation_group = ConversationGroup()
        self.all_items: dict[
            str, tp_rt.ConversationItem, 
        ] = {}  # main conversation and OOB; no trash
        self.responses: dict[
            str, tp_rt.RealtimeResponse, 
        ] = {}
        self.server_events: dict[str, tuple[
            tp_rt.RealtimeServerEvent, datetime, 
        ]] = {}
        self.client_events: dict[str, tuple[
            tp_rt.RealtimeClientEventParam, datetime, 
        ]] = {}
        self.impatience = __class__.Impatience(self)
        self.init_time = datetime.now()

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        self.server_events[event.event_id] = (event, datetime.now())
        match event:
            case tp_rt.ConversationItemCreatedEvent():
                raise RuntimeError('Beta API signature detected. Hint: are you a time traveler?')
            case tp_rt.ConversationItemAdded(item=item):
                self.impatience.handle(event)
            case (
                tp_rt.ConversationItemDone(item=item) |
                tp_rt.ResponseOutputItemDoneEvent(item=item)
            ):
                assert item.id is not None
                old_item = self.all_items[item.id]
                try:
                    old_item.status = item.status   # type: ignore
                except AttributeError:
                    pass
                assert old_item == item, (old_item, item)
                self.conversation_group.touch(item.id, event.event_id)
            case ConversationItemRetrieved(item=item):
                assert item.id is not None
                assert item.id in self.all_items
                self.all_items[item.id] = item
                self.conversation_group.touch(item.id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionCompletedEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                old_part = item.content[event.content_index]
                old_part.transcript = event.transcript
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionDeltaEvent():
                if event.delta:
                    item = self.all_items[event.item_id]
                    assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                    assert event.content_index is not None
                    old_part = item.content[event.content_index]
                    if old_part.transcript is None:
                        old_part.transcript = event.delta
                    else:
                        old_part.transcript += event.delta
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionFailedEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                old_part = item.content[event.content_index]
                old_part.transcript = str(event.error)
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemTruncatedEvent():
                cell = self.conversation_group.get_cell_from_id(event.item_id)
                # cell.audio_truncate is potentially already set by local.  
                cell.audio_truncate = (
                    event.content_index, event.audio_end_ms,
                )
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemDeletedEvent():
                self.conversation_group.touch(event.item_id, event.event_id)
                self.conversation_group.trash(event.item_id)
            case tp_rt.ResponseTextDeltaEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemAssistantMessage)
                content = item.content[event.content_index]
                if content.text is None:
                    content.text = event.delta
                else:
                    content.text += event.delta
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ResponseAudioTranscriptDeltaEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemAssistantMessage)
                content = item.content[event.content_index]
                if content.transcript is None:
                    content.transcript = event.delta
                else:
                    content.transcript += event.delta
                self.conversation_group.touch(event.item_id, event.event_id)
            case tp_rt.ResponseCreatedEvent(response=response):
                # Openai doesn't give us the main conversation ID.  
                # Hence the following assert.  
                # Thanks to the assert, assume non-None id to mean main conversation.  
                if response.conversation_id is not None:
                    self.conversation_group.assert_main_conversation_id(
                        response.conversation_id,
                    )
                assert response.id is not None
                assert response.id not in self.responses
                self.responses[response.id] = response
            case tp_rt.ResponseOutputItemAddedEvent():
                self.impatience.handle(event)
            case tp_rt.ResponseContentPartAddedEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, (
                    tp_rt.RealtimeConversationItemAssistantMessage, 
                    # tp_rt.RealtimeConversationItemFunctionCall, 
                ))
                assert len(item.content) == event.content_index
                item.content.append(tp_rt.realtime_conversation_item_assistant_message.Content(
                    audio=event.part.audio,
                    text=event.part.text,
                    transcript=event.part.transcript,
                    type=PART_TO_CONTENT_TYPE[
                        event.part.type
                    ] if event.part.type is not None else None,
                ))
                assert self.conversation_group.get_cell_from_id(
                    event.item_id,
                ).response_id == event.response_id
            case tp_rt.ResponseContentPartDoneEvent():
                item = self.all_items[event.item_id]
                assert isinstance(item, (
                    tp_rt.RealtimeConversationItemAssistantMessage, 
                    # tp_rt.RealtimeConversationItemFunctionCall, 
                ))
                assert len(item.content) > event.content_index
                assert self.conversation_group.get_cell_from_id(
                    event.item_id,
                ).response_id == event.response_id
            case tp_rt.ResponseDoneEvent(response=response):
                assert response.id is not None
                assert response.id in self.responses
                self.responses[response.id] = response
        return event, metadata
    
    @roster_manager.decorate
    def client_event_handler(
        self, event_param: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        event_id = event_param.get('event_id', None)
        if event_id is not None:
            self.client_events[event_id] = (event_param, datetime.now())
        event = parse_client_event_param(event_param)
        match event:
            case tp_rt.ConversationItemCreateEvent():
                event_param = tp.cast(
                    tp_rt.ConversationItemCreateEventParam, event_param,
                )
                item_id = event_param['item'].get(
                    'id', None, 
                ) or f'client-set-{uuid.uuid4()}'[:31]
                previous_item_id = event_param.get(
                    'previous_item_id', 
                    self.conversation_group.last_item_id(), 
                )
                e_p = deepcopy(event_param)
                e_p['item']['id'] = item_id
                e_p['previous_item_id'] = previous_item_id
                self.impatience.handle(e_p)
                return e_p, metadata
        return event_param, metadata

    def repr_cell(self, cell: ConversationGroup.Cell):
        buf = ['']
        buf.append('current state:')
        item = self.all_items[cell.item_id]
        buf.append(f'  {str_item_omit_audio(item)}')
        if cell.audio_truncate is not None:
            content_index, audio_end_ms = cell.audio_truncate
            buf.append(f'truncate: {content_index = }, {audio_end_ms = }')
        if cell.response_id is not None:
            metadata = self.responses[cell.response_id].metadata
            buf.append(f'metadata: {metadata}')
        buf.append('touched by:')
        for event_id in cell.touched_by_event_ids:
            if event_id is None:
                buf.append('  <unindexed client event>')
                continue
            try:
                event,       datetime_ = self.server_events[event_id]
            except KeyError:
                event_param, datetime_ = self.client_events[event_id]
                str_event = str_client_event_omit_audio(event_param)
            else:
                str_event = str_server_event_omit_audio(event)
            dt = (datetime_ - self.init_time).total_seconds()
            buf.append(f'  [{dt:5.1f}] {event_id:28s} {str_event}')
        return '\n  '.join(buf)[1:]
    
    def print_conversation(self, print_fn: tp.Callable = print) -> None:
        print_fn('<conversation_group>')
        print_fn('<main conversation>')
        for i, cell in enumerate(self.conversation_group.iter_main_conversation()):
            print_fn('-' * 8, i, '-' * 8)
            print_fn(self.repr_cell(cell))
        print_fn('</main conversation>')
        if self.conversation_group.out_of_band_cells:
            print_fn('<out-of-band items>')
            for i, cell in enumerate(self.conversation_group.out_of_band_cells.values()):
                print_fn('-' * 8, i, '-' * 8)
                print_fn(self.repr_cell(cell))
            print_fn('</out-of-band items>')
        print_fn('</conversation_group>')
