from datetime import datetime

import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_server_event import ConversationItemRetrieved

from .shared import (
    strItemOmitAudio, strServerEventOmitAudio, 
    parse_client_event_param, 
)
from .conversation import Conversation

class TrackConversation:
    '''
    Client-side repr of the conversation.  
    - Does not keep:  
      - assistant audio;  
      - user input audio;  
      - UNLESS retrieved via ConversationItemRetrievedEvent.  
        - That API is explicitly for retrieving full audio anyways.  
    - Does not track:  
      - response objects `tp_rt.RealtimeResponse`.  
    - Locally synced by watching outgoing client events:  
      - tool output.  
    - Fast synced via delta:  
      - user input audio transcription;  
      - assistant text;  
      - assistant audio transcript.  
    - Slowly synced, disregarding delta:  
      - assistant tool call.  
    '''
    def __init__(self):
        self.conversation = Conversation()
        self.items: dict[str, tp_rt.ConversationItem] = {}
        self.server_events: dict[str, tuple[
            tp_rt.RealtimeServerEvent, datetime, 
        ]] = {}
        self.init_time = datetime.now()

    def repr_cell(self, cell: Conversation.Cell):
        buf = ['']
        buf.append('current state:')
        item = self.items[cell.item_id]
        buf.append(f'  {strItemOmitAudio(item)}')
        if cell.audio_truncate is not None:
            content_index, audio_end_ms = cell.audio_truncate
            buf.append(f'truncate: {content_index = }, {audio_end_ms = }')
        buf.append('touched by:')
        for event_id in cell.touched_by_events:
            event, datetime_ = self.server_events[event_id]
            dt = (datetime_ - self.init_time).total_seconds()
            buf.append(f'''  {
                dt:5.1f
            } {event_id:28s} {strServerEventOmitAudio(event)}''')
        return '\n  '.join(buf)[1:]

    def add_item(
        self, item: tp_rt.ConversationItem,
        previous_item_id: str | None,
        event_id: str,
        is_from_server: bool,
    ) -> None:
        assert item.id is not None
        already_here = item.id in self.items
        assert is_from_server or not already_here
        self.items[item.id] = item
        if already_here:
            self.conversation.move(item.id, previous_item_id)
        else:
            self.conversation.insert_after(
                item.id, previous_item_id, 
            )
        self.conversation.touch(item.id, event_id)
    
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, _, 
    ) -> None:
        datetime_ = datetime.now()
        self.server_events[event.event_id] = (event, datetime_)
        match event:
            case tp_rt.ConversationItemCreatedEvent():
                raise RuntimeError('Beta API signature detected')
            case tp_rt.ConversationItemAdded(item=item):
                self.add_item(
                    item, event.previous_item_id, event.event_id, 
                    is_from_server=True,
                )
            case tp_rt.ConversationItemDone(item=item):
                assert item.id is not None
                old_item = self.items[item.id]
                try:
                    old_item.status = item.status   # type: ignore[attr-defined]
                except AttributeError:
                    pass
                assert old_item == item, (old_item, item)
                self.conversation.touch(item.id, event.event_id)
            case ConversationItemRetrieved(item=item):
                assert item.id is not None
                assert item.id in self.items
                self.items[item.id] = item
                self.conversation.touch(item.id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionCompletedEvent():
                item = self.items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                old_part = item.content[event.content_index]
                old_part.transcript = event.transcript
                self.conversation.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionDeltaEvent():
                if event.delta:
                    item = self.items[event.item_id]
                    assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                    assert event.content_index is not None
                    old_part = item.content[event.content_index]
                    if old_part.transcript is None:
                        old_part.transcript = event.delta
                    else:
                        old_part.transcript += event.delta
                self.conversation.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemInputAudioTranscriptionFailedEvent():
                item = self.items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemUserMessage)
                old_part = item.content[event.content_index]
                old_part.transcript = str(event.error)
                self.conversation.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemTruncatedEvent():
                cell = self.conversation.getCellFromId(event.item_id)
                assert cell.audio_truncate is None
                cell.audio_truncate = (
                    event.content_index, event.audio_end_ms,
                )
                self.conversation.touch(event.item_id, event.event_id)
            case tp_rt.ConversationItemDeletedEvent():
                self.conversation.touch(event.item_id, event.event_id)
                self.conversation.trash(event.item_id)
            case tp_rt.ResponseTextDeltaEvent():
                item = self.items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemAssistantMessage)
                content = item.content[event.content_index]
                if content.text is None:
                    content.text = event.delta
                else:
                    content.text += event.delta
                self.conversation.touch(event.item_id, event.event_id)
            case tp_rt.ResponseAudioTranscriptDeltaEvent():
                item = self.items[event.item_id]
                assert isinstance(item, tp_rt.RealtimeConversationItemAssistantMessage)
                content = item.content[event.content_index]
                if content.transcript is None:
                    content.transcript = event.delta
                else:
                    content.transcript += event.delta
                self.conversation.touch(event.item_id, event.event_id)
    
    def client_event_handler(
        self, event_param: tp_rt.RealtimeClientEventParam, _, 
    ) -> tp_rt.RealtimeClientEventParam:
        event = parse_client_event_param(event_param)
        match event:
            case tp_rt.ConversationItemCreateEvent():
                self.add_item(
                    event.item, 
                    event.previous_item_id or self.conversation.last_item_id(), 
                    '',
                    is_from_server=False,
                )
        return event_param
