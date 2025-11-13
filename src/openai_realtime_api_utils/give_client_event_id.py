from itertools import count

import openai.types.realtime as tp_rt

class GiveClientEventId:
    def __init__(self):
        self.c = count()
    
    def handleClientEvent(
        self, 
        eventParam: tp_rt.RealtimeClientEventParam, 
        _, 
    ) -> tp_rt.RealtimeClientEventParam:
        if 'event_id' in eventParam:
            return eventParam
        eventParam_ = eventParam.copy()
        eventParam_['event_id'] = f'client-{next(self.c):05d}-auto'
        return eventParam_
