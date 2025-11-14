# OpenAI Realtime API Utils
- See the example at [./tests/test_middlewares.py](./tests/test_middlewares.py)  
```python
with hook_handlers(
    connection, 
    serverEventHandlers = [
        track_config.server_event_handler,
        track_conversation.server_event_handler,
        middlewares.PrintEvents().server_event_handler,
        f, 
    ], 
    clientEventHandlers = [
        middlewares.GiveClientEventId().client_event_handler, 
        track_config.client_event_handler,
        track_conversation.client_event_handler,
        middlewares.PrintEvents().client_event_handler,
    ],
) as (keep_receiving, send):
    asyncio.create_task(keep_receiving())
    ...
```

- `openai_realtime_api_utils.hook_handlers`: run a session with your handlers.  
- `openai_realtime_api_utils.middlewares`
  - `.TrackConfig`: Keep track of session config.  
  - `.TrackConversation`: Client-side representation of the conversation(s), synced by events.  
  - `.GiveClientEventId`: Auto-fill client event id.  
  - `.PrintEvents`: Print events for debug.  

## Style
- Functional programming.
- Dependency injection. 
- Middleware event handlers.  
