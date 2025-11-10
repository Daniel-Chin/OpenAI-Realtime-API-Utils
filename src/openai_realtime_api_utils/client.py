from contextlib import asynccontextmanager
from itertools import count
import asyncio
import inspect
from enum import Enum
from functools import wraps
import time

from .shared import *
from .interface import Interface, BaseHandler, MODEL
from openai_realtime_api import defaults
from .conversation import Conversation

F = tp.TypeVar('F', bound=tp.Callable[..., tp.Any])

class Side(Enum):
    SERVER = 'server'
    CLIENT = 'client'

class Client(BaseHandler):
    def __init__(self, interface: Interface):
        '''
        Don't use this constructor directly. Use `with Client.Context(...) as client:` instead.  
        If you are in a hurry, it's also ok to `Client.Open(...)`.  
        '''
        self.interface = interface

        self.lock = asyncio.Lock()

        # A shallow copy of all events we received.  
        self.event_logs: tp.Dict[Side, tp.Dict[EventID, tp.Dict[str, tp.Any]]] = {
            Side.SERVER: {}, 
            Side.CLIENT: {}, 
        }

        self.server_sessionConfig: SessionConfig = SessionConfig(
            ResponseConfig(
                OMIT, OMIT, OMIT, OMIT, OMIT, OMIT, OMIT, OMIT, 
            ), 
            OMIT, OMIT, OMIT, 
        )
        self.items: tp.Dict[ItemID, ConversationItem] = {}
        self.server_conversation = Conversation()
        self.server_responses: tp.Dict[ResponseID, Response] = {}
        
        self.eventIdCount = count()

        def clientEventDecorator(f: F) -> F:
            g = self.log(Side.CLIENT)(f)
            h = self.autoFillEventId(g)
            return h
        
        self.sessionUpdate            = clientEventDecorator(self.interface.sessionUpdate)
        self.inputAudioBufferAppend   = clientEventDecorator(self.interface.inputAudioBufferAppend)
        self.inputAudioBufferCommit   = clientEventDecorator(self.interface.inputAudioBufferCommit)
        self.inputAudioBufferClear    = clientEventDecorator(self.interface.inputAudioBufferClear)
        self.conversationItemCreate   = clientEventDecorator(self.interface.conversationItemCreate)
        self.conversationItemTruncate = clientEventDecorator(self.interface.conversationItemTruncate)
        self.conversationItemDelete   = clientEventDecorator(self.interface.conversationItemDelete)
        self.responseCreate           = clientEventDecorator(self.interface.responseCreate)
        self.responseCancel           = clientEventDecorator(self.interface.responseCancel)
    
    @classmethod
    @asynccontextmanager
    async def Context(
        cls, api_key: str, sessionConfig: SessionConfig = defaults.SESSION_CONFIG, 
        model: str = MODEL, 
    ):
        client = cls.__new__(cls)
        try:
            async with Interface.Context(api_key, client, model) as interface:
                client.__init__(interface)
                await interface.sessionUpdate(sessionConfig, client.nextEventId())
                yield client
        finally:
            client.cleanup()
    
    @classmethod
    async def Open(
        cls, api_key: str, sessionConfig: SessionConfig = defaults.SESSION_CONFIG, 
        model: str = MODEL, 
    ):
        warnings.warn('Why not use Client.Context() instead?')
        client = cls.__new__(cls)
        interface = await Interface.Open(api_key, client, model)
        client.__init__(interface)
        await interface.sessionUpdate(sessionConfig, client.nextEventId())
        return client
    
    async def close(self):
        await self.interface.close()
        self.cleanup()
    
    def cleanup(self):
        pass    # no cleanup needed yet

    def nextEventId(self) -> EventID:
        return f'ibc_{next(self.eventIdCount) : 04d}'
        # [i]nitiated [b]y [c]lient
    
    def autoFillEventId(self, f: F) -> F:
        formal_params = inspect.signature(f).parameters.keys()
        @wraps(f)
        def wrapper(*args, **kw):
            for k, v in zip(formal_params, args):
                assert k not in kw
                kw[k] = v
            if 'event_id' not in kw:
                kw['event_id'] = self.nextEventId()
            return f(*args, **kw)
        return tp.cast(F, wrapper)

    @staticmethod
    def log(side: Side):
        def decorator(f: F) -> F:
            sig = inspect.signature(f)
            formal_params = sig.parameters.keys()
            @wraps(f)
            def wrapper(self: __class__, *args, **kw):
                result = f(self, *args, **kw)
                for k, v in zip(formal_params, args):
                    assert k not in kw
                    kw[k] = v
                event_id = kw['event_id']
                event_log = self.event_logs[side]
                assert event_id not in event_log
                kw[METHOD_NAME] = f.__name__
                kw[TIMESTAMP] = time.time()
                event_log[event_id] = kw
                return result
            return tp.cast(F, wrapper)
        return decorator
    
    def seekEventLog(self, event_id: EventID):
        for side in Side:
            try:
                return side, self.event_logs[side][event_id]
            except KeyError:
                pass
        raise KeyError(event_id)
    
    def reprCell(self, cell: Conversation.Cell):
        buf = ['']
        if cell.audio_truncate is not None:
            content_index, audio_end_ms = cell.audio_truncate
            buf.append(f'truncate: {content_index = }, {audio_end_ms = }')
        buf.append('modified by:')
        for event_id in cell.modified_by:
            event_log = self.event_logs[Side.SERVER][event_id]
            buf.append(f'''  {
                event_log[TIMESTAMP]:5.1f
            } {event_id:28s} {event_log[METHOD_NAME]}''')
        return '\n  '.join(buf)[1:]
    
    @log(Side.SERVER)
    def onError(
        self, event_id: EventID, 
        error: OpenAIError, do_warn: bool = True, 
    ):
        if do_warn:
            error.warn()
    
    def updateServerSessionConfig(self, sessionConfig: SessionConfig):
        if sessionConfig != self.server_sessionConfig:
            new = deepWithoutOmits(sessionConfig            .asPrimitive())
            old = deepWithoutOmits(self.server_sessionConfig.asPrimitive())
            updated = deepUpdate(old, new)
            assert isinstance(updated, dict)
            with MustDrain(updated) as (u, mutate):
                self.server_sessionConfig, r = SessionConfig.fromPrimitive(u)
                mutate(r)
    
    @log(Side.SERVER)
    def onSessionCreated(
        self, event_id: EventID, 
        sessionConfig: SessionConfig,
        session_id: str, model: str, 
    ):
        self.updateServerSessionConfig(sessionConfig)

    @log(Side.SERVER)
    def onSessionUpdated(
        self, event_id: EventID, 
        sessionConfig: SessionConfig,
        session_id: str, model: str, 
    ):
        self.updateServerSessionConfig(sessionConfig)
    
    @log(Side.SERVER)
    def onConversationCreated(
        self, event_id: EventID, 
        conversation_id: str, 
    ):
        pass
    
    @log(Side.SERVER)
    def onConversationItemCreated(
        self, event_id: EventID, 
        previous_item_id: ItemID, item: ConversationItem, 
    ):
        assert item.id_ not in self.items
        self.items[item.id_] = item
        self.server_conversation.insertAfter(
            item.id_, previous_item_id, 
        )
        self.server_conversation.touched(item.id_, event_id)
    
    @log(Side.SERVER)
    def onConversationItemInputAudioTranscriptionCompleted(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, transcript: str, 
    ):
        old_part = self.items[item_id].content[content_index]
        assert old_part.transcript is None
        new_part = ContentPart(
            old_part.type_, 
            old_part.text, 
            old_part.audio, 
            transcript, 
        )
        self.items[item_id] = self.items[item_id].withUpdatedContentPart(
            content_index, new_part, 
        )
        self.server_conversation.touched(item_id, event_id)
    
    @log(Side.SERVER)
    def onConversationItemInputAudioTranscriptionFailed(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, error: OpenAIError, 
    ):
        super().onConversationItemInputAudioTranscriptionFailed(
            event_id, item_id, content_index, error,
        )
    
    @log(Side.SERVER)
    def onConversationItemTruncated(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, audio_end_ms: int, 
    ):
        self.server_conversation.cells[item_id].audio_truncate = (
            content_index, audio_end_ms, 
        )

    @log(Side.SERVER)
    def onConversationItemDeleted(
        self, event_id: EventID, 
        item_id: ItemID, 
    ):
        self.server_conversation.pop(item_id)
    
    @log(Side.SERVER)
    def onInputAudioBufferCommitted(
        self, event_id: EventID, 
        previous_item_id: ItemID, item_id: ItemID,
    ):
        self.server_conversation.insertAfter(item_id, previous_item_id)

    @log(Side.SERVER)
    def onInputAudioBufferCleared(
        self, event_id: EventID, 
    ):
        pass
    
    @log(Side.SERVER)
    def onInputAudioBufferSpeechStarted(
        self, event_id: EventID, 
        audio_start_ms: int, item_id: ItemID,
    ):
        pass
    
    @log(Side.SERVER)
    def onInputAudioBufferSpeechStopped(
        self, event_id: EventID, 
        audio_end_ms: int, item_id: ItemID,
    ):
        pass
    
    @log(Side.SERVER)
    def onResponseCreated(
        self, event_id: EventID, 
        response_id: ResponseID, 
    ):
        self.server_responses[response_id] = Response(
            response_id, Status.IN_PROGRESS, None, (), None, 
        )
    
    @log(Side.SERVER)
    def onResponseDone(
        self, event_id: EventID, 
        response: Response, items: tp.Tuple[ConversationItem, ...], 
    ):
        self.server_responses[response.id_] = response
        for theirs in items:
            mine = self.items[theirs.id_]
            pM = deepWithout((None, OMIT, NOT_HERE), mine  .asPrimitive())
            pT = deepWithout((None, OMIT, NOT_HERE), theirs.asPrimitive())
            assert deepUpdate(pM, pT) == pM

    @log(Side.SERVER)
    def onResponseOutputItemAdded(
        self, event_id: EventID, 
        response_id: ResponseID, output_index: int, 
        item: ConversationItem, 
    ):
        old_response = self.server_responses[response_id]
        assert len(old_response.output) == output_index
        self.server_responses[response_id] = Response(
            old_response.id_,
            old_response.status,
            old_response.status_details,
            old_response.output + (item.id_, ),
            old_response.usage,
        )
        assert item.id_ not in self.items
        self.items[item.id_] = item
        self.server_conversation.touched(item.id_, event_id)

    @log(Side.SERVER)
    def onResponseOutputItemDone(
        self, event_id: EventID, 
        response_id: ResponseID, output_index: int, 
        item: ConversationItem, 
    ):
        assert self.server_responses[response_id].output[output_index] == item.id_
        self.items[item.id_] = item

    def updateContentPart(
        self, by_event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        part: ContentPart, 
    ):
        assert self.server_responses[response_id].output[output_index] == item_id
        self.items[item_id] = self.items[
            item_id
        ].withUpdatedContentPart(content_index, part)
        self.server_conversation.touched(item_id, by_event_id)

    @log(Side.SERVER)
    def onResponseContentPartAdded(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        part: ContentPart, 
    ):
        self.updateContentPart(
            event_id, 
            response_id, item_id, output_index, content_index, part,
        )
    
    @log(Side.SERVER)
    def onResponseContentPartDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        part: ContentPart, 
    ):
        self.updateContentPart(
            event_id, 
            response_id, item_id, output_index, content_index, part,
        )
    
    @log(Side.SERVER)
    def onResponseTextDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        pass

    @log(Side.SERVER)
    def onResponseTextDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        text: str, 
    ):
        self.updateContentPart(
            event_id, 
            response_id, item_id, output_index, content_index,
            ContentPart(ContentPartType.TEXT, text),
        )

    @log(Side.SERVER)
    def onResponseAudioTranscriptDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        pass

    @log(Side.SERVER)
    def onResponseAudioTranscriptDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        transcript: str, 
    ):
        try:
            old_part = self.items[item_id].content[content_index]
        except (KeyError, IndexError):
            new_part = ContentPart(
                ContentPartType.AUDIO, None, NOT_HERE, transcript, 
            )
        else:
            new_part = ContentPart(
                old_part.type_, 
                old_part.text,
                old_part.audio,
                transcript,
            )
        self.updateContentPart(
            event_id, 
            response_id, item_id, output_index, content_index,
            new_part,
        )

    @log(Side.SERVER)
    def onResponseAudioDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        pass

    @log(Side.SERVER)
    def onResponseAudioDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
    ):
        try:
            old_part = self.items[item_id].content[content_index]
        except (KeyError, IndexError):
            new_part = ContentPart(
                ContentPartType.AUDIO, None, NOT_HERE, None, 
            )
        else:
            new_part = ContentPart(
                old_part.type_, 
                old_part.text,
                NOT_HERE,
                old_part.transcript,
            )
        self.updateContentPart(
            event_id, 
            response_id, item_id, output_index, content_index,
            new_part,
        )

    @log(Side.SERVER)
    def onResponseFunctionCallArgumentsDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        call_id: str, delta: str, 
    ):
        pass

    @log(Side.SERVER)
    def onResponseFunctionCallArgumentsDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        call_id: str, arguments: str, 
    ):
        _ = content_index   # meaningless
        assert self.server_responses[response_id].output[output_index] == item_id
        old_item = self.items[item_id]
        assert old_item.call_id == call_id
        self.items[item_id] = ConversationItem(
            old_item.id_, 
            old_item.type_, 
            old_item.status, 
            old_item.role, 
            old_item.content, 
            old_item.call_id, 
            old_item.name, 
            arguments, 
            old_item.output, 
        )
        self.server_conversation.touched(item_id, event_id)

    @log(Side.SERVER)
    def onRateLimitsUpdated(
        self, event_id: EventID, 
        rateLimits: tp.Tuple[RateLimit, ...], 
    ):
        pass

def deepUpdate(
    old: dict | tuple | list, 
    new: dict | tuple | list, 
/):
    '''
    No circular safegaurd.  
    '''
    if (
        isinstance(old, dict) and 
        isinstance(new, dict)
    ):
        d = {**old}
        for k, v in new.items():
            if k in d and (
                isinstance(v, dict) or 
                isinstance(v, tuple) or 
                isinstance(v, list)
            ):
                d[k] = deepUpdate(d[k], v)
            else:
                d[k] = v
        return d
    if (
        isinstance(old, list) and 
        isinstance(new, list)
    ):
        l = []
        for i, v in enumerate(new):
            if i < len(old):
                if (
                    isinstance(v, dict) or 
                    isinstance(v, tuple) or 
                    isinstance(v, list)
                ):
                    l.append(deepUpdate(old[i], v))
                else:
                    l.append(v)
            else:
                l.append(v)
        return l
    if (
        isinstance(old, tuple) and 
        isinstance(new, tuple)
    ):
        return tuple(deepUpdate(list(old), list(new)))
    raise TypeError(f'{old = }, {new = }')
