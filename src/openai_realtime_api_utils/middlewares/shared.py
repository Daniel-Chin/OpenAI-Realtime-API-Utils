import typing as tp
import functools

HANDLER_ROSTER = 'HANDLER_ROSTER'

F = tp.TypeVar('F', bound=tp.Callable[[
    tp.Any, tp.Any, dict, tp.Any,
], tuple[tp.Any, dict]])

class MetadataHandlerRosterManager:
    def __init__(
        self, handler_name: str, 
        allow_same_handler_repeated: bool = False, 
    ):
        self.handler_name = handler_name
        self.allow_same_handler_repeated = allow_same_handler_repeated
    
    def decorate(self, handler: F) -> F:
        @functools.wraps(handler)
        def wrapper(
            self_, event, metadata: dict, connection, 
        ) -> tuple[tp.Any, dict]:
            if HANDLER_ROSTER not in metadata:
                metadata[HANDLER_ROSTER] = []
            roster = metadata[HANDLER_ROSTER]
            assert isinstance(roster, list)
            if not self.allow_same_handler_repeated:
                assert self.handler_name not in roster
            roster.append(self.handler_name)
            return handler(self_, event, metadata, connection)
        return tp.cast(F, wrapper)
    
    def is_in_roster(
        self, handler_name: str, metadata: dict, 
    ) -> bool:
        '''
        Check if another handler has already processed this event.  
        '''
        if HANDLER_ROSTER not in metadata:
            return False
        roster = metadata[HANDLER_ROSTER]
        assert isinstance(roster, list)
        return handler_name in roster

AsyncOnSpeechEndHandler = tp.Annotated[
    tp.Callable[[
        tp.Annotated[str, 'item_id'], 
        tp.Annotated[int, 'content_index'], 
    ], tp.Coroutine[None, None, None]], 
    'called when speech ends. Not called when interrupted.', 
]
