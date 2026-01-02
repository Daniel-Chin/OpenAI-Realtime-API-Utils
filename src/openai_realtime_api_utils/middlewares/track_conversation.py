from __future__ import annotations

from datetime import datetime
import typing as tp
from copy import deepcopy
from contextlib import suppress
import json

import uuid
import openai.types.realtime as tp_rt

from ..shared import (
    str_item_omit_audio, parse_client_event_param, 
    item_from_param, PART_TO_CONTENT_TYPE, 
    merge_content_parts_transcript, 
)
from .shared import MetadataHandlerRosterManager
from ..conversation_group import ConversationGroup
from ..b64_decode_cachable import b64_decode_cachable

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
                # What may differ >>>>
                with suppress(AttributeError):
                    old_item.status    = item.status     # type: ignore
                with suppress(AttributeError):
                    old_item.arguments = item.arguments  # type: ignore
                # <<<<
                assert old_item == item, (old_item, item)
                self.conversation_group.touch(item.id, event.event_id)
            # case ConversationItemRetrieved(item=item):    # ufortunately contains less info than can be inferred from client side.  
            #     assert item.id is not None
            #     assert item.id in self.all_items
            #     self.all_items[item.id] = item
            #     self.conversation_group.touch(item.id, event.event_id)
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
                if cell.truncate_info is None:
                    # Unreachable?
                    cell.truncate_info = (
                        event.content_index, event.audio_end_ms, None, 
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
            case tp_rt.ResponseAudioDeltaEvent():
                n_new_bytes = len(b64_decode_cachable(event, metadata))
                cell = self.conversation_group.get_cell_from_id(event.item_id)
                cell.audio_total_bytes += n_new_bytes
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

    def print_cell(
        self, cell: ConversationGroup.Cell, 
        verbose: bool,
        print_fn: tp.Callable, 
    ):
        item = self.all_items[cell.item_id]
        if verbose:
            print_fn('current state:')
            print_fn(f'  {str_item_omit_audio(item)}')
            if cell.truncate_info is not None:
                content_index, audio_end_ms, truncated_transcript = cell.truncate_info
                print_fn(f'truncate: {content_index = }, {audio_end_ms = }, ~{truncated_transcript!r}')
            if cell.response_id is not None:
                metadata = self.responses[cell.response_id].metadata
                print_fn(f'metadata: {metadata}')
            print_fn('touched by:')
            for event_id in cell.touched_by_event_ids:
                if event_id is None:
                    print_fn('  <unindexed client event>')
                    continue
                try:
                    event,       datetime_ = self.server_events[event_id]
                except KeyError:
                    event_param, datetime_ = self.client_events[event_id]
                    str_event = event_param['type']
                else:
                    str_event = type(event).__name__
                dt = (datetime_ - self.init_time).total_seconds()
                print_fn(f'  [{dt:5.1f}] {event_id:28s} {str_event}')
        else:
            match item:
                case tp_rt.RealtimeConversationItemUserMessage():
                    print_fn('User:', repr(merge_content_parts_transcript(item.content)))
                case tp_rt.RealtimeConversationItemAssistantMessage():
                    if cell.truncate_info is None:
                        print_fn('Assistant:', repr(merge_content_parts_transcript(item.content)))
                    else:
                        _, _, truncated_transcript = cell.truncate_info
                        print_fn('Assistant: ~', repr(truncated_transcript), '...', sep='')
                case tp_rt.RealtimeConversationItemFunctionCall():
                    print_fn('Assistant:', end=' ')
                    self.print_function_call(item, print_fn)
                case tp_rt.RealtimeConversationItemFunctionCallOutput():
                    print_fn('Tool:', repr(item.output))
                case _:
                    print_fn('Unknown item type:', type(item))
    
    def print_function_call(
        self, item: tp_rt.RealtimeConversationItemFunctionCall, 
        print_fn: tp.Callable,
        end: str = '\n',
    ) -> None:
        kwargs: dict[str, tp.Any] = json.loads(item.arguments)
        print_fn(item.name, '(', sep='', end='')
        for k, v in kwargs.items():
            print_fn(k, '=', repr(v), sep='', end=', ')
        print_fn(')', end=end)
    
    def print_conversation(
        self, 
        verbose: bool = True,
        print_fn: tp.Callable = print, 
    ) -> None:
        def cell_print(*a, **kw):
            if verbose:
                print_fn(' ' * 4, end='')
            print_fn(*a, **kw)
        
        def print_cells(cells: tp.Iterable[ConversationGroup.Cell]) -> None:
            for i, cell in enumerate(cells):
                if verbose:
                    print_fn(' ', '-' * 8, i, '-' * 8)
                self.print_cell(cell, verbose=verbose, print_fn=cell_print)
        
        # print_fn('<conversation_group>')
        print_fn('<main conversation>')
        print_cells(self.conversation_group.iter_main_conversation())
        print_fn('</main conversation>')
        if self.conversation_group.out_of_band_cells:
            print_fn('<out-of-band items>')
            print_cells(self.conversation_group.out_of_band_cells.values())
            print_fn('</out-of-band items>')
        # print_fn('</conversation_group>')
