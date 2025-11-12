'''
Deprecated in favor of openai.realtime.realtime
'''

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import typing as tp
import json

import pydantic
import websockets
from websockets.asyncio.client import ClientConnection
import openai.types.realtime as tp_rt
from agents.util._types import (
    MaybeAwaitable, 
)
from agents.exceptions import (
    UserError, 
)
from agents.realtime import (
    RealtimeModelTracingConfig, 
)
from agents.realtime.openai_realtime import (
    _USER_AGENT,
    get_server_event_type_adapter, get_api_key,
    _ConversionHelper, AllRealtimeServerEvents,
)

class InterfaceException(Exception):
    def __init__(self, exception: Exception, context: str | None = None) -> None:
        super().__init__(f'InterfaceException: {context}: {exception}' if context else str(exception))

EventHandler = tp.Callable[[tp_rt.RealtimeServerEvent | InterfaceException], None]

class BasicConfig(tp.TypedDict):
    '''Options for connecting to a realtime model.'''

    api_key: tp.NotRequired[str | tp.Callable[[], MaybeAwaitable[str]]]
    '''The API key (or function that returns a key) to use when connecting. If unset, the model will
    try to use a sane default. For example, the OpenAI Realtime model will try to use the
    `OPENAI_API_KEY`  environment variable.
    '''

    url: tp.NotRequired[str]
    '''The URL to use when connecting. If unset, the model will use a sane default. For example,
    the OpenAI Realtime model will use the default OpenAI WebSocket URL.
    '''

    headers: tp.NotRequired[dict[str, str]]
    '''The headers to use when connecting. If unset, the model will use a sane default.
    Note that, when you set this, authorization header won't be set under the hood.
    e.g., {'api-key': 'your api key here'} for Azure OpenAI Realtime WebSocket connections.
    '''

    initial_model_settings: tp.NotRequired[tp_rt.RealtimeSessionCreateRequest]
    '''The initial model settings to use when connecting.'''

    tracing_config: tp.NotRequired[RealtimeModelTracingConfig | tp.Literal['auto']]

class Interface:
    '''
    Based on https://github.com/openai/openai-agents-python/blob/73e7843075acaae25bc56628ab8017ae6a4e13f7/src/agents/realtime/openai_realtime.py#L186
    '''
    def __init__(self) -> None:
        self._websocket: ClientConnection | None = None
        self._websocket_task: asyncio.Task[None] | None = None
        self._handlers: list[EventHandler] = []
        self._tracing_config: RealtimeModelTracingConfig | tp.Literal['auto'] | None = None
        self._session_config: tp_rt.RealtimeSessionCreateRequest | None = None
        self._server_event_type_adapter = get_server_event_type_adapter()
    
    @asynccontextmanager
    async def context(
        self, basic_config: BasicConfig, 
        server_event_handlers: list[EventHandler], 
    ) -> tp.AsyncGenerator[Interface, None]:
        '''An async context manager for the model connection.'''
        self.set_event_handlers(server_event_handlers)
        await self.connect(basic_config)
        try:
            yield self
        finally:
            await self.close()

    async def connect(
        self, basic_config: BasicConfig, 
    ) -> None:
        '''Establish a connection to the model and keep it alive.'''
        assert self._websocket is None, 'Already connected'
        assert self._websocket_task is None, 'Already connected'

        api_key = await get_api_key(basic_config.get('api_key'))

        self._tracing_config = basic_config.get('tracing_config')
        self._session_config = basic_config.get('initial_model_settings')

        model = 'gpt-realtime' if self._session_config is None else self._session_config.model

        url = f'wss://api.openai.com/v1/realtime?model={model}'

        def get_headers() -> dict[str, str]:
            h = basic_config.get('headers')
            if h is not None:
                return h
            else:
                # OpenAI's Realtime API
                if not api_key:
                    raise UserError('API key is required but was not provided.')
                return {'Authorization': f'Bearer {api_key}'}
        
        self._websocket = await websockets.connect(
            url,
            user_agent_header=_USER_AGENT,
            additional_headers=get_headers(),
            max_size=None,  # Allow any size of message
        )
        self._websocket_task = asyncio.create_task(self._listen_for_messages())
        if self._session_config is not None:
            await self.send(tp_rt.SessionUpdateEvent(
                type='session.update', session=self._session_config, 
            ))

    async def _send_tracing_config(self) -> None:
        '''Update tracing configuration via session.update event.'''
        if self._tracing_config is not None:
            converted_tracing_config = _ConversionHelper.convert_tracing_config(self._tracing_config)
            await self.send(
                tp_rt.SessionUpdateEvent(
                    type='session.update',
                    session=tp_rt.RealtimeSessionCreateRequest(
                        model=self._session_config.model if self._session_config else None,
                        type='realtime',
                        tracing=converted_tracing_config,
                    ),
                )
            )

    def set_event_handlers(self, hs: list[EventHandler], /) -> None:
        '''Set the handlers.'''
        self._handlers = hs
    
    def add_event_handler(self, h: EventHandler, /) -> None:
        '''Add a handler.'''
        self._handlers.append(h)
    
    def _handle(self, event: tp_rt.RealtimeServerEvent | InterfaceException) -> None:
        for h in self._handlers:
            h(event)

    async def _listen_for_messages(self):
        assert self._websocket is not None, 'Not connected'

        try:
            async for message in self._websocket:
                try:
                    parsed = json.loads(message)
                    await self._handle_ws_event(parsed)
                except json.JSONDecodeError as e:
                    self._handle(InterfaceException(
                        exception=e, context='Failed to parse WebSocket message as JSON',
                    ))
                except Exception as e:
                    self._handle(InterfaceException(
                        exception=e, context='Error handling WebSocket event',
                    ))

        except websockets.exceptions.ConnectionClosedOK:
            # Normal connection closure - no exception event needed
            print('WebSocket connection closed normally')
        except websockets.exceptions.ConnectionClosed as e:
            self._handle(InterfaceException(
                exception=e, context='WebSocket connection closed unexpectedly'
            ))
        except Exception as e:
            self._handle(InterfaceException(
                exception=e, context='WebSocket error in message listener'
            ))

    async def send(self, event: tp_rt.RealtimeClientEvent) -> None:
        '''Send a raw message to the model.'''
        assert self._websocket is not None, 'Not connected'
        payload = event.model_dump_json(exclude_none=True, exclude_unset=True)
        print(f'{payload = }')
        await self._websocket.send(payload)

    async def close(self) -> None:
        '''Close the session.'''
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        if self._websocket_task:
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                pass
            self._websocket_task = None

    async def _handle_ws_event(self, event: dict[str, tp.Any]):
        try:
            if 'previous_item_id' in event and event['previous_item_id'] == '':
                event['previous_item_id'] = None
            parsed: AllRealtimeServerEvents = self._server_event_type_adapter.validate_python(event)
        except pydantic.ValidationError as e:
            self._handle(InterfaceException(
                exception=e, context=f'Failed to validate server event: {event}',
            ))
            return
        except Exception as e:
            self._handle(InterfaceException(
                exception=e, context=f'Failed to validate server event: {event}',
            ))
            return

        match parsed:
            case tp_rt.SessionCreatedEvent():
                await self._send_tracing_config()
            case tp_rt.SessionUpdatedEvent():
                self._update_created_session(parsed.session)
        
        self._handle(parsed)

    def _update_created_session(
        self,
        session: tp_rt.RealtimeSessionCreateRequest | tp_rt.RealtimeTranscriptionSessionCreateRequest,
    ) -> None:
        if isinstance(session, tp_rt.RealtimeSessionCreateRequest):
            self._session_config = session
