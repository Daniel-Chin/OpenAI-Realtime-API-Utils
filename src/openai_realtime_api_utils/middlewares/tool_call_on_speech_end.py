import asyncio
import typing as tp
from contextlib import contextmanager

import openai.types.realtime as tp_rt

from .shared import MetadataHandlerRosterManager, AsyncOnSpeechEndHandler

class ToolCallOnSpeechEnd:
    '''
    Deprecated.  
    This middleware was developed out of the belief that an assistant message could contain 
    both speech and tool call, similar to ChatCompletion. But I was wrong.  

    Original doc:  
    Emit an event of the tool call when the audio player finishes playing.  
    '''

    roster_manager = MetadataHandlerRosterManager('ToolCallOnSpeechEnd')

    def __init__(
        self, 
        tool_call_handlers: list[
            tp.Callable[[
                tp_rt.ResponseFunctionCallArgumentsDoneEvent, 
            ], tp.Coroutine[None, None, None] | None]
        ],
        register_on_speech_end_handler: tp.Callable[
            [AsyncOnSpeechEndHandler], tp.Callable[[], None],
        ], 
    ):
        self.tool_call_handlers = tool_call_handlers
        self.unregister = register_on_speech_end_handler(
            self.on_speech_end, 
        )

        self._tool_calls_in_waiting = dict[tp.Annotated[
            str, 'item_id', 
        ], tp_rt.ResponseFunctionCallArgumentsDoneEvent]()
        self.ended_item_ids = set[str]()
    
    @contextmanager
    def context(self):
        try:
            yield self
        finally:
            self.unregister()

    @roster_manager.decorate
    def server_event_handler(
        self, event: tp_rt.RealtimeServerEvent, metadata: dict, _,
    ) -> tuple[tp_rt.RealtimeServerEvent, dict]:
        if isinstance(event, tp_rt.ResponseFunctionCallArgumentsDoneEvent):
            if event.item_id in self.ended_item_ids:
                # Already ended, call immediately.  
                self._handle(event)
            else:
                assert event.item_id not in self._tool_calls_in_waiting
                self._tool_calls_in_waiting[event.item_id] = event
        return event, metadata
    
    async def on_speech_end(self, item_id: str, content_index: int) -> None:
        self.ended_item_ids.add(item_id)
        try:
            event = self._tool_calls_in_waiting.pop(item_id)
        except KeyError:
            return
        self._handle(event)
    
    def _handle(
        self, event: tp_rt.ResponseFunctionCallArgumentsDoneEvent,
    ) -> None:
        for handler in self.tool_call_handlers:
            match handler(event):
                case tp.Coroutine() as coro:
                    asyncio.create_task(coro)
                case None:
                    pass
