from itertools import count

import openai.types.realtime as tp_rt

from .shared import MetadataHandlerRosterManager

class GiveClientEventId:
    roster_manager = MetadataHandlerRosterManager('GiveClientEventId')

    def __init__(self):
        self.c = count()
    
    @roster_manager.decorate
    def client_event_handler(
        self, 
        eventParam: tp_rt.RealtimeClientEventParam, 
        metadata: dict, _, 
    ) -> tuple[tp_rt.RealtimeClientEventParam, dict]:
        if 'event_id' in eventParam:
            return eventParam, metadata
        eventParam_ = eventParam.copy()
        eventParam_['event_id'] = f'client-{next(self.c):05d}-auto'
        return eventParam_, metadata
