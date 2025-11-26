# OpenAI Realtime API Utils
- See the example at [./tests/test_middlewares.py](./tests/test_middlewares.py)  
```python
with hook_handlers(
    connection, 
    serverEventHandlers = [
        track_config.server_event_handler,  # views session.updated
        track_conversation.server_event_handler,    # views various events
        *iap_server_handlers,   # views e.g. response.output_audio.delta
        stream_mic.server_event_handler,    # views session.updated
        middlewares.PrintEvents().server_event_handler, # views all events
        f, 
    ], 
    clientEventHandlers = [
        middlewares.GiveClientEventId().client_event_handler, # alter all events without ID
        track_config.client_event_handler,  # views session.update
        track_conversation.client_event_handler,    # views various events
        middlewares.PrintEvents().client_event_handler, # views all events
    ],
) as (keep_receiving, send):
    iap_register_send_with_handlers(send)   # needs to send interrupt events
    stream_mic.register_send_with_handlers(send)    # needs to send audio input
    
    asyncio.create_task(keep_receiving())
...
```

- `openai_realtime_api_utils.hook_handlers`: run a session with your handlers.  
- `openai_realtime_api_utils.middlewares`
  - `.TrackConfig`: Keep track of session config.  
  - `.TrackConversation`: Client-side representation of the conversation(s), synced by events.  
  - `.GiveClientEventId`: Auto-fill client event id.  
  - `.Interrupt`: The user may interrupt assistant speech.  
  - `.AudioPlayer`: Host system audio playback.  
  - `.StreamMic`: Host system audio capture.  
  - `.interruptable_audio_player`: `.Interrupt` and `.AudioPlayer` in gift wraps.  
  - `.PrintEvents`: Print events for debug.  

## Style
- Functional programming.
- Dependency injection. 
- Middleware event handlers.  
