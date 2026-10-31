"""Read-only index access: no migrations, schema maintenance or implicit creation."""

import sqlite3
import time
from pathlib import Path

from catia_manual.core.index import ManualIndex
from catia_manual.core.index_schema import SCHEMA_VERSION


class ReadOnlyIndex(ManualIndex):
    def __init__(self, database, root, *, timeout=5):
        self.database, self.root = Path(database).resolve(), Path(root).resolve()
        self.module_labels = {}
        # mode=ro refuses missing files. query_only also blocks TEMP writes.
        self.connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + timeout
            self.connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
            self.connection.execute("BEGIN")
            if self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("索引版本不匹配，请先在查看器中更新索引")
            if Path(self.meta("root", "")).resolve() != self.root:
                raise ValueError("文档库挂载不一致，请先在查看器中验证文档副本")
            self.library_id = self.meta("library_id")
            if not self.library_id:
                raise ValueError("索引缺少文档库身份")
        except BaseException:
            self.connection.close()
            raise
