from __future__ import annotations

from dataclasses import dataclass

class Conversation:
    @dataclass
    class Cell:
        item_id: str
        # (content_index, audio_end_ms)
        audio_truncate: tuple[int, int] | None = None
        touched_by_events: list[str] = field(default_factory=list)

    def __init__(self) -> None:
        self.cells: list[Conversation.Cell] = []
        self.trashed_cells: list[Conversation.Cell] = []
    
    def seek(self, item_id: str) -> int:
        cell_i, = [
            i for i, cell in enumerate(self.cells) 
            if cell.item_id == item_id
        ]
        return cell_i

    def getCellFromId(self, item_id: str) -> Cell:
        return self.cells[self.seek(item_id)]
    
    def insert_after(
        self, item_id: str, 
        previous_item_id: str | None, 
    ) -> Cell:
        new_cell = self.Cell(item_id)
        self.cells.insert((
            0 if previous_item_id is None 
            else self.seek(previous_item_id) + 1
        ), new_cell)
        return new_cell
    
    def move(
        self, item_id: str, 
        previous_item_id: str | None, 
    ) -> None:
        cell = self.cells.pop(self.seek(item_id))
        self.cells.insert((
            0 if previous_item_id is None 
            else self.seek(previous_item_id) + 1
        ), cell)
    
    def previousItemIdOf(self, item_id: str) -> str | None:
        cell_i = self.seek(item_id)
        if cell_i == 0:
            return None
        else:
            return self.cells[cell_i - 1].item_id
    
    def trash(self, item_id: str) -> None:
        self.trashed_cells.append(self.cells.pop(self.seek(item_id)))
    
    def touch(self, item_id: str, event_id: str) -> None:
        self.getCellFromId(item_id).touched_by_events.append(event_id)
    
    def last_item_id(self) -> str | None:
        if len(self.cells) == 0:
            return None
        else:
            return self.cells[-1].item_id
