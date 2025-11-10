from __future__ import annotations

import typing as tp
import asyncio
from contextlib import asynccontextmanager
import json
import warnings
import websockets

from .shared import *

MODEL = 'gpt-4o-realtime-preview-2024-10-01'    # The only available model at this time. When Realtime API leaves beta, there will prolly be a way to point to a stable one.

ENDPOINT = 'wss://api.openai.com/v1/realtime'

class Interface:
    def __init__(
        self, ws: websockets.WebSocketClientProtocol, 
        handler: BaseHandler,
    ):
        '''
        Don't use this constructor directly. Use `with Interface.Context(...) as interface:` instead.  
        If you are in a hurry, it's also ok to `Interface.Open(...)`.  
        '''
        self.ws = ws
        self.handler = handler

        self.sendMiddleware = identity
        self.closed_by_me = False

    @classmethod
    @asynccontextmanager
    async def Context(cls, api_key: str, handler: BaseHandler, model: str = MODEL):
        async with cls.__newWebSocket(api_key, model) as ws:
            interface = cls(ws, handler)
            try:
                yield interface
            finally:
                interface.closed_by_me = True
    
    @classmethod
    async def Open(cls, api_key: str, handler: BaseHandler, model: str = MODEL):
        warnings.warn('Why not use Interface.Context() instead?')
        ws = await cls.__newWebSocket(api_key, model)
        return cls(ws, handler)
    
    async def close(self):
        self.closed_by_me = True
        await self.ws.close()
    
    @staticmethod
    def __newWebSocket(api_key: str, model: str = MODEL):
        return websockets.connect(
            ENDPOINT + '?model=' + model, 
            extra_headers={
                'Authorization': 'Bearer ' + api_key,
                'OpenAI-Beta': 'realtime=v1',
            },
        )

    async def __omitJsonSend(self, event: tp.Dict):
        await self.ws.send(json.dumps(
            self.sendMiddleware(
                deepWithoutOmits(event), 
            ), 
        ))

    async def sessionUpdate(
        self, 
        sessionConfig: SessionConfig, 
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{SESSION}.{UPDATE}',
            'session': sessionConfig.asPrimitive(), 
        }
        await self.__omitJsonSend(event)
    
    async def inputAudioBufferAppend(
        self, audio: str, event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{INPUT_AUDIO_BUFFER}.{APPEND}',
            'audio': audio,
        }
        await self.__omitJsonSend(event)
    
    async def inputAudioBufferCommit(
        self, event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{INPUT_AUDIO_BUFFER}.{COMMIT}',
        }
        await self.__omitJsonSend(event)
    
    async def inputAudioBufferClear(
        self, event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{INPUT_AUDIO_BUFFER}.{CLEAR}',
        }
        await self.__omitJsonSend(event)
    
    async def conversationItemCreate(
        self, 
        item: ConversationItem, 
        previous_item_id: ItemID | OmitType = OMIT,
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{CONVERSATION}.{ITEM}.{CREATE}',
            'previous_item_id': previous_item_id,
            'item': item.asPrimitive(),
        }
        await self.__omitJsonSend(event)
    
    async def conversationItemTruncate(
        self, 
        item_id: ItemID,
        content_index: int | OmitType = OMIT,
        audio_end_ms: int | OmitType = OMIT,
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{CONVERSATION}.{ITEM}.{TRUNCATE}',
            'item_id': item_id,
            'content_index': content_index,
            'audio_end_ms': audio_end_ms,
        }
        await self.__omitJsonSend(event)
    
    async def conversationItemDelete(
        self, 
        item_id: ItemID,
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{CONVERSATION}.{ITEM}.{DELETE}',
            'item_id': item_id,
        }
        await self.__omitJsonSend(event)
    
    async def responseCreate(
        self, 
        responseConfig: ResponseConfig | OmitType = OMIT, 
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{RESPONSE}.{CREATE}',
            'response': OMIT if isinstance(
                responseConfig, OmitType, 
            ) else responseConfig.asPrimitive(),
        }
        await self.__omitJsonSend(event)
    
    async def responseCancel(
        self, 
        event_id: EventID | OmitType = OMIT,
    ):
        event = {
            'event_id': event_id,
            'type': f'{RESPONSE}.{CANCEL}',
        }
        await self.__omitJsonSend(event)
    
    async def receiveLoop(self):
        try:
            async for message in self.ws:
                event: tp.Dict = json.loads(message)
                await self.parseEvent(event)
        except websockets.ConnectionClosed:
            if not self.closed_by_me:
                raise
        except asyncio.CancelledError:
            pass

    async def parseEvent(self, event: tp.Dict):
        with MustDrain(event) as (e, mutateE):
            event_type: str = e.pop('type')
            event_id = e.pop('event_id')
            if event_type == ERROR:
                with MustDrain(e.pop('error')) as (error_primitive, mutateErr):
                    error, r = OpenAIError.fromPrimitive(error_primitive)
                    mutateErr(r)
                self.handler.onError(
                    event_id, error, 
                )
            elif event_type in (
                f'{SESSION}.{CREATED}', 
                f'{SESSION}.{UPDATED}', 
            ):
                with MustDrain(e.pop('session')) as (session_primitive, mutateSession):
                    session_id = session_primitive.pop('id')
                    model = session_primitive.pop('model')
                    assert session_primitive.pop('object') == f'{REALTIME}.{SESSION}'
                    sessionConfig, r = SessionConfig.fromPrimitive(session_primitive)
                    mutateSession(r)
                    if event_type == f'{SESSION}.{CREATED}':
                        self.handler.onSessionCreated(
                            event_id, 
                            sessionConfig, 
                            session_id, model, 
                        )
                    else:
                        self.handler.onSessionUpdated(
                            event_id, 
                            sessionConfig, 
                            session_id, model, 
                        )
            elif event_type == f'{CONVERSATION}.{CREATED}':
                with MustDrain(e.pop('conversation')) as (conversation, _):
                    assert conversation.pop('object') == f'{REALTIME}.{CONVERSATION}'
                    self.handler.onConversationCreated(
                            event_id, conversation.pop('id'), 
                        )
            elif event_type == f'{CONVERSATION}.{ITEM}.{CREATED}':
                with MustDrain(e.pop('item')) as (item_primitive, mutateItem):
                    assert item_primitive.pop('object') == f'{REALTIME}.{ITEM}'
                    conversationItem, r = ConversationItem.fromPrimitive(item_primitive)
                    mutateItem(r)
                self.handler.onConversationItemCreated(
                    event_id, 
                    e.pop('previous_item_id'), 
                    conversationItem, 
                )
            elif event_type == f'{CONVERSATION}.{ITEM}.{INPUT_AUDIO_TRANSCRIPTION}.{COMPLETED}':
                self.handler.onConversationItemInputAudioTranscriptionCompleted(
                    event_id, 
                    e.pop('item_id'), 
                    e.pop('content_index'), 
                    e.pop('transcript'), 
                )
            elif event_type == f'{CONVERSATION}.{ITEM}.{INPUT_AUDIO_TRANSCRIPTION}.{FAILED}':
                with MustDrain(e.pop('error')) as (error_primitive, mutateErr):
                    error, r = OpenAIError.fromPrimitive(error_primitive)
                    mutateErr(r)
                self.handler.onConversationItemInputAudioTranscriptionFailed(
                    event_id, 
                    e.pop('item_id'), 
                    e.pop('content_index'), 
                    error, 
                )
            elif event_type == f'{CONVERSATION}.{ITEM}.{TRUNCATED}':
                self.handler.onConversationItemTruncated(
                    event_id, 
                    e.pop('item_id'), 
                    e.pop('content_index'), 
                    e.pop('audio_end_ms'), 
                )
            elif event_type == f'{CONVERSATION}.{ITEM}.{DELETED}':
                self.handler.onConversationItemDeleted(
                    event_id, 
                    e.pop('item_id'), 
                )
            elif event_type == f'{INPUT_AUDIO_BUFFER}.{COMMITTED}':
                self.handler.onInputAudioBufferCommitted(
                    event_id, 
                    e.pop('previous_item_id'), 
                    e.pop('item_id'), 
                )
            elif event_type == f'{INPUT_AUDIO_BUFFER}.{CLEARED}':
                self.handler.onInputAudioBufferCleared(
                    event_id, 
                )
            elif event_type == f'{INPUT_AUDIO_BUFFER}.{SPEECH_STARTED}':
                self.handler.onInputAudioBufferSpeechStarted(
                    event_id, 
                    e.pop('audio_start_ms'), 
                    e.pop('item_id'), 
                )
            elif event_type == f'{INPUT_AUDIO_BUFFER}.{SPEECH_STOPPED}':
                self.handler.onInputAudioBufferSpeechStopped(
                    event_id, 
                    e.pop('audio_end_ms'), 
                    e.pop('item_id'), 
                )
            elif event_type == f'{RESPONSE}.{CREATED}':
                with MustDrain(e.pop('response')) as (response, _):
                    response_id = response.pop('id')
                    assert response.pop('object') == f'{REALTIME}.{RESPONSE}'
                    # In the particular case of response.created, the following fields don't matter.  
                    response.pop('status', None)
                    response.pop('status_details', None)
                    response.pop('output', None)
                    response.pop('usage', None)
                self.handler.onResponseCreated(
                    event_id, 
                    response_id, 
                )
            elif event_type == f'{RESPONSE}.{DONE}':
                with MustDrain(e.pop('response')) as (response_primitive, mutateResponse):
                    response, items, r = Response.fromPrimitive(response_primitive)
                    mutateResponse(r)
                self.handler.onResponseDone(
                    event_id, 
                    response,
                    items, 
                )
            elif event_type in (
                f'{RESPONSE}.{OUTPUT_ITEM}.{ADDED}', 
                f'{RESPONSE}.{OUTPUT_ITEM}.{DONE}',
            ):
                with MustDrain(e.pop('item')) as (item_primitive, mutateItem):
                    assert item_primitive.pop('object') == f'{REALTIME}.{ITEM}'
                    conversationItem, r = ConversationItem.fromPrimitive(item_primitive)
                    mutateItem(r)
                response_id = e.pop('response_id')
                output_index = e.pop('output_index')
                if event_type == f'{RESPONSE}.{OUTPUT_ITEM}.{ADDED}':
                    self.handler.onResponseOutputItemAdded(
                        event_id, 
                        response_id, output_index, conversationItem, 
                    )
                else:
                    self.handler.onResponseOutputItemDone(
                        event_id, 
                        response_id, output_index, conversationItem, 
                    )
            elif event_type in (
                f'{RESPONSE}.{CONTENT_PART}.{ADDED}', 
                f'{RESPONSE}.{CONTENT_PART}.{DONE}',
            ):
                with MustDrain(e.pop('part')) as (part_primitive, mutatePart):
                    assert part_primitive.pop('object') == f'{REALTIME}.{CONTENT_PART}'
                    contentPart, r = ContentPart.fromPrimitive(part_primitive)
                    mutatePart(r)
                response_id = e.pop('response_id')
                item_id = e.pop('item_id')
                output_index = e.pop('output_index')
                content_index = e.pop('content_index')
                if event_type == f'{RESPONSE}.{CONTENT_PART}.{ADDED}':
                    self.handler.onResponseContentPartAdded(
                        event_id, 
                        response_id, item_id, output_index, 
                        content_index, contentPart, 
                    )
                else:
                    self.handler.onResponseContentPartDone(
                        event_id, 
                        response_id, item_id, output_index, 
                        content_index, contentPart, 
                    )
            elif event_type.startswith(f'{RESPONSE}.'):
                kw = {
                    'event_id': event_id,
                    'response_id':   e.pop('response_id'), 
                    'item_id':       e.pop('item_id'),
                    'output_index':  e.pop('output_index'),
                    'content_index': e.pop('content_index'),
                }
                if event_type == f'{RESPONSE}.{TEXT}.{DELTA}':
                    self.handler.onResponseTextDelta(
                        **kw, 
                        delta=e.pop('delta'), 
                    )
                elif event_type == f'{RESPONSE}.{TEXT}.{DONE}':
                    self.handler.onResponseTextDone(
                        **kw, 
                        text=e.pop('text'), 
                    )
                elif event_type == f'{RESPONSE}.{AUDIO_TRANSCRIPT}.{DELTA}':
                    self.handler.onResponseAudioTranscriptDelta(
                        **kw, 
                        delta=e.pop('delta'), 
                    )
                elif event_type == f'{RESPONSE}.{AUDIO_TRANSCRIPT}.{DONE}':
                    self.handler.onResponseAudioTranscriptDone(
                        **kw,
                        transcript=e.pop('transcript'),
                    )
                elif event_type == f'{RESPONSE}.{AUDIO}.{DELTA}':
                    self.handler.onResponseAudioDelta(
                        **kw, 
                        delta=e.pop('delta'), 
                    )
                elif event_type == f'{RESPONSE}.{AUDIO}.{DONE}':
                    self.handler.onResponseAudioDone(
                        **kw, 
                    )
                elif event_type == f'{RESPONSE}.{FUNCTION_CALL_ARGUMENTS}.{DELTA}':
                    self.handler.onResponseFunctionCallArgumentsDelta(
                        **kw, 
                        call_id=e.pop('call_id'), 
                        delta=e.pop('delta'), 
                    )
                elif event_type == f'{RESPONSE}.{FUNCTION_CALL_ARGUMENTS}.{DONE}':
                    self.handler.onResponseFunctionCallArgumentsDone(
                        **kw, 
                        call_id=e.pop('call_id'), 
                        arguments=e.pop('arguments'), 
                    )
                else:
                    raise ValueError(event_type)
            elif event_type == f'{RATE_LIMITS}.{UPDATED}':
                rate_limits_primitive = e.pop('rate_limits')
                rateLimits = []
                for rate_limit_primitive in rate_limits_primitive:
                    with MustDrain(rate_limit_primitive) as (rlp, mutateRLP):
                        rateLimit, r = RateLimit.fromPrimitive(rlp)
                        rateLimits.append(rateLimit)
                        mutateRLP(r)
                self.handler.onRateLimitsUpdated(
                    event_id, 
                    tuple(rateLimits), 
                )
            else:
                raise ValueError(event_type)

class BaseHandler:
    '''
    - Inherit this class and override the methods.  
      - The handlers are synchronous and should return fast.  
    '''
    def onError(
        self, event_id: EventID, 
        error: OpenAIError,
    ):
        '''
        Override this. 
        '''
        error.warn()
    
    def onSessionCreated(
        self, event_id: EventID, 
        sessionConfig: SessionConfig,
        session_id: str, model: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onSessionUpdated(
        self, event_id: EventID, 
        sessionConfig: SessionConfig,
        session_id: str, model: str, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onConversationCreated(
        self, event_id: EventID, 
        conversation_id: str, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onConversationItemCreated(
        self, event_id: EventID, 
        previous_item_id: ItemID, item: ConversationItem, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onConversationItemInputAudioTranscriptionCompleted(   # I blame OpenAI
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, transcript: str, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onConversationItemInputAudioTranscriptionFailed(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, error: OpenAIError, 
    ):
        '''
        Override this. 
        '''
        # If you overrided `...TranscriptionCompleted()`, you should override this too. Hence the default fatal exception.  
        error.throw()
    
    def onConversationItemTruncated(
        self, event_id: EventID, 
        item_id: ItemID, content_index: int, audio_end_ms: int, 
    ):
        '''
        Override this. 
        '''
        pass

    def onConversationItemDeleted(
        self, event_id: EventID, 
        item_id: ItemID, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onInputAudioBufferCommitted(
        self, event_id: EventID, 
        previous_item_id: ItemID, item_id: ItemID,
    ):
        '''
        Override this. 
        '''
        pass

    def onInputAudioBufferCleared(
        self, event_id: EventID, 
    ):
        '''
        Override this. 
        '''
        pass

    def onInputAudioBufferSpeechStarted(
        self, event_id: EventID, 
        audio_start_ms: int, item_id: ItemID,
    ):
        '''
        Override this. 
        '''
        pass
    
    def onInputAudioBufferSpeechStopped(
        self, event_id: EventID, 
        audio_end_ms: int, item_id: ItemID,
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseCreated(
        self, event_id: EventID, 
        response_id: ResponseID, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onResponseDone(
        self, event_id: EventID, 
        response: Response, items: tp.Tuple[ConversationItem, ...],
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseOutputItemAdded(
        self, event_id: EventID, 
        response_id: ResponseID, output_index: int, 
        item: ConversationItem, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseOutputItemDone(
        self, event_id: EventID, 
        response_id: ResponseID, output_index: int, 
        item: ConversationItem, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseContentPartAdded(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        part: ContentPart, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onResponseContentPartDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        part: ContentPart, 
    ):
        '''
        Override this. 
        '''
        pass
    
    def onResponseTextDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseTextDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        text: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseAudioTranscriptDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseAudioTranscriptDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        transcript: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseAudioDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        delta: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseAudioDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
    ):
        '''
        Override this. 
        '''
        pass

    def onResponseFunctionCallArgumentsDelta(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        call_id: str, delta: str, 
    ):
        '''
        Ignore this. 
        You'd have to have 0 chill to optimize your function to take streaming arguments.
        '''
        pass

    def onResponseFunctionCallArgumentsDone(
        self, event_id: EventID, 
        response_id: ResponseID, item_id: ItemID,
        output_index: int, content_index: int, 
        call_id: str, arguments: str, 
    ):
        '''
        Override this. 
        '''
        pass

    def onRateLimitsUpdated(
        self, event_id: EventID, 
        rateLimits: tp.Tuple[RateLimit, ...], 
    ):
        '''
        Override this. 
        '''
        pass
