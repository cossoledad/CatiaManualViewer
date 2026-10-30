"""Validated local resource actions, shared by every presentation surface."""

import os
import subprocess
from pathlib import Path

from catia_manual.core.paths import within_root

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff"}


class ResourceService:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, relative: str, *, must_exist=True) -> Path:
        path = within_root(self.root, self.root / relative)
        if must_exist and not path.is_file():
            raise FileNotFoundError(f"文件不存在：{relative}")
        return path

    def image(self, relative: str) -> Path:
        path = self.path(relative)
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("此资源不是支持的图片")
        return path

    def reveal(self, relative: str):
        path = self.path(relative)
        if os.name != "nt":
            raise OSError("资源管理器定位仅支持 Windows")
        return subprocess.Popen(
            ["explorer.exe", "/select,", str(path)],
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
