from __future__ import annotations

from dataclasses import dataclass

from .shared import *

class Conversation:
    @dataclass
    class Cell:
        parent: Conversation
        prev: ItemID | None
        next_: ItemID | None

        # (content_index, audio_end_ms)
        audio_truncate: tp.Tuple[int, int] | None = None
        modified_by: tp.List[EventID] = []
    
        def nextCell(self):
            if self.next_ is None:
                raise StopIteration
            return self.parent.cells[self.next_]

    def __init__(self):
        self.cells: tp.Dict[ItemID, Conversation.Cell] = {}
        self.root: ItemID | None = None
    
    def insertAfter(
        self, item_id: ItemID, 
        previous_item_id: ItemID | None, 
    ):
        if previous_item_id is None:
            assert self.root is None
            self.root = item_id
            cell = self.Cell(self, None, None)
            self.cells[item_id] = cell
            return cell
        prevCell = self.cells[previous_item_id]
        next_id = prevCell.next_
        cell = self.Cell(self, previous_item_id, next_id)
        self.cells[item_id] = cell
        prevCell.next_ = item_id
        if next_id is not None:
            self.cells[next_id].prev = item_id
        return cell
    
    def pop(self, item_id: ItemID, /):
        cell = self.cells.pop(item_id)
        prev_id = cell.prev
        next_id = cell.next_
        if prev_id is not None:
            self.cells[prev_id].next_ = next_id
        if next_id is not None:
            self.cells[next_id].prev = prev_id
        if item_id == self.root:
            self.root = next_id
    
    def __iter__(self):
        item_id = self.root
        while item_id is not None:
            cell = self.cells[item_id]
            yield item_id, cell
            item_id = cell.next_
    
    def touched(self, item_id: ItemID, event_id: EventID):
        cell = self.cells[item_id]
        cell.modified_by.append(event_id)
