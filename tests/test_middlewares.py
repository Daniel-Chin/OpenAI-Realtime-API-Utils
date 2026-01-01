# ruff: noqa: F401, F841

import os
import sys
import asyncio
import io
import logging
from contextlib import suppress

import openai
from openai.resources.realtime import AsyncRealtime
import openai.types.realtime as tp_rt
from openai.types.realtime.realtime_audio_input_turn_detection_param import SemanticVad
from agents.realtime import RealtimePlaybackTracker
from daniel_chin_python_alt_stdlib.select_audio_device import (
    select_audio_device_input, select_audio_device_output,
)

from openai_realtime_api_utils import hook_handlers, middlewares
from openai_realtime_api_utils.middlewares.log_events import unexpected_error_only
from openai_realtime_api_utils.pyaudio_utils import py_audio_context
from openai_realtime_api_utils.audio_config import EXAMPLE_SPECIFICATION

LOG_PATH = './tests/logs/test_middlewares.log'

def FILTER_SERVER(_): return True
def FILTER_CLIENT(_): return True

# FILTER_SERVER = unexpected_error_only
# def FILTER_CLIENT(_): return False

def init_logging():
    _logger = logging.getLogger()
    _logger.setLevel(logging.DEBUG)

    with suppress(FileNotFoundError):
        os.remove(LOG_PATH)
    
    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s\n')
    file_handler.setFormatter(file_format)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_format = logging.Formatter('%(levelname)s: %(message)s')
    stream_handler.setFormatter(stream_format)

    _logger.addHandler(file_handler)
    _logger.addHandler(stream_handler)

    for downstream in [
        'openai', 
        'websockets', 
    ]:
        logging.getLogger(downstream).setLevel(logging.WARNING)

    return _logger

logger = init_logging()

async def main():
    a_oa = openai.AsyncOpenAI()
    a_r = AsyncRealtime(a_oa)

    def log_config(e, metadata, _):
        if not FILTER_SERVER(e):
            return e, metadata
        buf = io.StringIO()
        def p(*a, **kw):
            print(*a, file=buf, **kw)
        p('<config>')
        if track_config.session_config is None:
            p('Unavailable, or invalidated.')
        else:
            p(track_config.session_config.model_dump())
        p('</config>')
        track_conversation.print_conversation(print_fn=p)
        logger.debug(buf.getvalue())
        return e, metadata
    
    with py_audio_context() as pa:
        device_in  = select_audio_device_input (pa)
        device_out = select_audio_device_output(pa)

        async with a_r.connect(
            model='gpt-realtime-mini',
        ) as connection:
            # All middlewares are optional.  
            track_config = middlewares.TrackConfig()
            track_conversation = middlewares.TrackConversation()
            playback_tracker = RealtimePlaybackTracker()
            log_events = middlewares.LogEvents(
                filter_server=FILTER_SERVER,
                filter_client=FILTER_CLIENT,
            )
            with (
                middlewares.interruptable_audio_player(
                    connection, 
                    playback_tracker,
                    track_config,
                    track_conversation,
                    pa, 
                    EXAMPLE_SPECIFICATION, 
                    output_device_index = device_out, 
                ) as (
                    audio_player, interrupt, 
                    iap_server_handlers, iap_register_send_with_handlers,
                ),
                middlewares.StreamMic(
                    pa, 
                    EXAMPLE_SPECIFICATION,
                    input_device_index = device_in,
                ).context() as stream_mic,
                hook_handlers(
                    connection, 
                    # All middlewares are optional.  
                    server_event_handlers = [
                        track_config.server_event_handler,  # views session.updated
                        track_conversation.server_event_handler,    # views various events
                        *iap_server_handlers,   # views e.g. response.output_audio.delta
                        stream_mic.server_event_handler,    # views session.updated
                        log_events.server_event_handler, # views all events
                        log_config, 
                    ], 
                    client_event_handlers = [
                        middlewares.GiveClientEventId().client_event_handler, # alter all events without ID
                        track_config.client_event_handler,  # views session.update
                        track_conversation.client_event_handler,    # views various events
                        log_events.client_event_handler, # views all events
                    ],
                ) as (keep_receiving, send),
            ):
                iap_register_send_with_handlers(send)   # needs to send interrupt events
                stream_mic.register_send_with_handlers(send)    # needs to send audio input
                
                asyncio.create_task(keep_receiving(), name = 'keep_receiving')

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
                                transcription=tp_rt.AudioTranscriptionParam(
                                    language='en',
                                    model='gpt-4o-mini-transcribe',
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

                try:
                    await asyncio.sleep(20)
                except asyncio.CancelledError:
                    print('bye.')

if __name__ == '__main__':
    asyncio.run(main())
