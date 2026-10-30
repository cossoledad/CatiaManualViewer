"""Reader preferences are global; page tasks own only their transient work."""

from dataclasses import dataclass

READING_MODES = ("original", "bilingual", "chinese")


@dataclass
class ReaderState:
    mode: str = "original"

    @classmethod
    def from_settings(cls, appearance):
        mode = appearance.get("reading_mode", "original")
        return cls(mode if mode in READING_MODES else "original")

    def select(self, mode):
        if mode not in READING_MODES:
            raise ValueError("无效的阅读模式")
        self.mode = mode
