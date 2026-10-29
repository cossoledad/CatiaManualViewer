from dataclasses import dataclass


@dataclass
class Location:
    path: str
    fragment: str = ""
    title: str = ""
    x: float = 0
    y: float = 0
    reading_anchor: dict | None = None


class NavigationHistory:
    """Browser-style history independent of GUI and rendered cache filenames."""

    def __init__(self, capacity: int = 200):
        self.entries: list[Location] = []
        self.position = -1
        self.capacity = capacity

    @property
    def current(self) -> Location | None:
        return self.entries[self.position] if self.position >= 0 else None

    @property
    def can_back(self) -> bool:
        return self.position > 0

    @property
    def can_forward(self) -> bool:
        return self.position + 1 < len(self.entries)

    def visit(self, location: Location) -> None:
        current = self.current
        if current and (current.path, current.fragment) == (location.path, location.fragment):
            return
        del self.entries[self.position + 1 :]
        self.entries.append(location)
        if len(self.entries) > self.capacity:
            self.entries.pop(0)
        self.position = len(self.entries) - 1

    def jump(self, position: int) -> Location:
        if not 0 <= position < len(self.entries):
            raise IndexError("历史位置无效")
        self.position = position
        return self.entries[position]

    def save_scroll(self, x: float, y: float) -> None:
        if self.current:
            self.current.x, self.current.y = max(0, x), max(0, y)
