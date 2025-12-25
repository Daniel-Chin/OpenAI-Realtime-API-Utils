# ruff: noqa: F401, F841

import os
import sys
import asyncio
import typing as tp
import io

import openai
from openai.resources.realtime import AsyncRealtime
import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_audio_input_turn_detection_param import SemanticVad
from agents.realtime import RealtimePlaybackTracker
from daniel_chin_python_alt_stdlib.select_audio_device import (
    select_audio_device_input, select_audio_device_output,
)

from openai_realtime_api_utils import hook_handlers, middlewares
from openai_realtime_api_utils.middlewares.print_events import unexpected_error_only
from openai_realtime_api_utils.pyaudio_utils import py_audio_context
from openai_realtime_api_utils.audio_config import EXAMPLE_SPECIFICATION

def FILTER_SERVER(_): return True
def FILTER_CLIENT(_): return True

# FILTER_SERVER = unexpected_error_only
# def FILTER_CLIENT(_): return False

LOG_STDOUT = os.getenv('LOG_STDOUT')
if LOG_STDOUT:
    class TeeOutput:
        def __init__(self, file_path: str, original_stream: io.TextIOBase | tp.Any):
            self.file = open(file_path, 'w', buffering=1)  # line-buffered
            self.original_stream = original_stream
        
        def write(self, message):
            self.original_stream.write(message)
            self.file.write(message)
        
        def flush(self):
            self.original_stream.flush()
            self.file.flush()
        
        def close(self):
            self.file.close()
    
    sys.stdout = TeeOutput('./tests/logs/test_middlewares.log', sys.stdout)
    # sys.stderr = TeeOutput('./tests/logs/test_middlewares.log', sys.stderr)

async def main():
    a_oa = openai.AsyncOpenAI()
    a_r = AsyncRealtime(a_oa)

    def f(e, metadata, _):
        if not FILTER_SERVER(e):
            return e, metadata
        print('<config>')
        if track_config.session_config is None:
            print('Unavailable, or invalidated.')
        else:
            print(track_config.session_config.model_dump())
        print('</config>')
        track_conversation.print_conversation()
        return e, metadata
    
    with py_audio_context() as pa:
        device_in  = select_audio_device_input (pa)
        device_out = select_audio_device_output(pa)

        async with a_r.connect(
            model='gpt-realtime-mini',
        ) as connection:
            track_config = middlewares.TrackConfig()
            track_conversation = middlewares.TrackConversation()
            playback_tracker = RealtimePlaybackTracker()
            print_events = middlewares.PrintEvents(
                filter_server=FILTER_SERVER,
                filter_client=FILTER_CLIENT,
            )
            with middlewares.interruptable_audio_player(
                connection, 
                playback_tracker,
                track_config,
                track_conversation,
                pa, 
                EXAMPLE_SPECIFICATION, 
                output_device_index = device_out, 
            ) as (audio_player, interrupt, iap_server_handlers, iap_register_send_with_handlers):
                with middlewares.StreamMic(
                    pa, 
                    EXAMPLE_SPECIFICATION,
                    input_device_index = device_in,
                ).context() as stream_mic:

                    with hook_handlers(
                        connection, 
                        server_event_handlers = [
                            track_config.server_event_handler,  # views session.updated
                            track_conversation.server_event_handler,    # views various events
                            *iap_server_handlers,   # views e.g. response.output_audio.delta
                            stream_mic.server_event_handler,    # views session.updated
                            print_events.server_event_handler, # views all events
                            f, 
                        ], 
                        client_event_handlers = [
                            middlewares.GiveClientEventId().client_event_handler, # alter all events without ID
                            track_config.client_event_handler,  # views session.update
                            track_conversation.client_event_handler,    # views various events
                            print_events.client_event_handler, # views all events
                        ],
                    ) as (keep_receiving, send):
                        iap_register_send_with_handlers(send)   # needs to send interrupt events
                        stream_mic.register_send_with_handlers(send)    # needs to send audio input
                        
                        asyncio.create_task(keep_receiving(), name = 'keep_receiving')

                        await story(send)

async def story(send: tp.Callable[[tp_rt.RealtimeClientEventParam], tp.Awaitable[None]]):
    await send(tp_rt.SessionUpdateEventParam(
        type='session.update',
        session=tp_rt.RealtimeSessionCreateRequestParam(
            type='realtime',
            audio=tp_rt.RealtimeAudioConfigParam(
                input=tp_rt.RealtimeAudioConfigInputParam(
                    turn_detection=SemanticVad(
                        type='semantic_vad',
                        create_response=True,
                        eagerness='high',
                        interrupt_response=True,
                    ),
                ),
            ),
        ),
    ))

    await send(tp_rt.ConversationItemCreateEventParam(
        type='conversation.item.create',
        item=tp_rt.RealtimeConversationItemUserMessageParam(
            type='message',
            role='user',
            content=[tp_rt.realtime_conversation_item_user_message_param.Content(
                type='input_text',
                text='What is three plus four? Be brief.',
            )],
        ),
    ))
    await send(tp_rt.ResponseCreateEventParam(
        type='response.create',
        response=tp_rt.RealtimeResponseCreateParamsParam(
            # conversation='none',
            metadata=dict(laser='69'),
            # output_modalities=['text'],
        ),
    ))

    await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
