from __future__ import annotations

from dataclasses import dataclass, field

from .shared import L, is_root

class ConversationGroup:
    @dataclass
    class Cell:
        item_id: str
        response_id: str | None = None
        # (content_index, audio_end_ms)
        audio_truncate: tuple[int, int] | None = None
        touched_by_event_ids: list[str | None] = field(default_factory=list)

    def __init__(self) -> None:
        self._main_conversation: list[ConversationGroup.Cell] = []
        self._main_conversation_item_ids: set[str] = set()
        self.main_conversation_id: str | None = None
        self.trashed_cells: list[ConversationGroup.Cell] = []
        self.out_of_band_cells: dict[str, ConversationGroup.Cell] = {}
    
    def assert_main_conversation_id(self, conversation_id: str) -> None:
        if self.main_conversation_id is None:
            self.main_conversation_id = conversation_id
        else:
            assert self.main_conversation_id == conversation_id
    
    def seek(self, item_id: str) -> int:
        cell_i, = [
            i for i, cell in enumerate(self._main_conversation) 
            if cell.item_id == item_id
        ]
        return cell_i

    def get_cell_from_id(self, item_id: str) -> Cell:
        if self.main_conversation_contains(item_id):
            return self._main_conversation[self.seek(item_id)]
        return self.out_of_band_cells[item_id]
    
    def index_after(
        self, previous_item_id: str | None, 
    ) -> int:
        if is_root(previous_item_id):
            return 0
        assert previous_item_id is not None
        return self.seek(previous_item_id) + 1
    
    def insert_after(
        self, cell: Cell,
        previous_item_id: str | None, 
    ) -> Cell:
        assert cell.item_id != L.root
        assert not [x for x in self.out_of_band_cells.values() if x.item_id == cell.item_id]
        self._main_conversation.insert(
            self.index_after(previous_item_id), cell, 
        )
        assert cell.item_id not in self._main_conversation_item_ids
        self._main_conversation_item_ids.add(cell.item_id)
        return cell
    
    def move(
        self, item_id: str, 
        previous_item_id: str | None, 
    ) -> Cell:
        cell = self._main_conversation.pop(self.seek(item_id))
        self._main_conversation.insert(
            self.index_after(previous_item_id), cell, 
        )
        return cell
    
    def previousItemIdOf(self, item_id: str) -> str:
        cell_i = self.seek(item_id)
        if cell_i == 0:
            return L.root
        else:
            return self._main_conversation[cell_i - 1].item_id
    
    def trash(self, item_id: str) -> None:
        self.trashed_cells.append(self._main_conversation.pop(self.seek(item_id)))
        self._main_conversation_item_ids.remove(item_id)
    
    def touch(self, item_id: str, event_id: str | None) -> None:
        self.get_cell_from_id(item_id).touched_by_event_ids.append(event_id)
    
    def last_item_id(self) -> str:
        if len(self._main_conversation) == 0:
            return L.root
        else:
            return self._main_conversation[-1].item_id
    
    def main_conversation_contains(self, item_id: str) -> bool:
        return item_id in self._main_conversation_item_ids
    
    def safe_add_oob(self, cell: Cell) -> Cell:
        assert not self.main_conversation_contains(cell.item_id)
        assert cell.item_id not in self.out_of_band_cells
        self.out_of_band_cells[cell.item_id] = cell
        return cell
