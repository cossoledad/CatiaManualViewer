"""Synchronize successful article navigation without changing the user's search scope."""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QTreeWidgetItem

from catia_manual.core.index import ManualIndex
from catia_manual.core.structure import StructureIndex
from catia_manual.core.terminology import chinese_label

LABELS = {
    "User Tasks": "用户任务",
    "Reference Information": "参考信息",
    "Getting Started": "快速入门",
    "Overview": "概览",
    "Workbench Description": "工作台说明",
    "What's New?": "新增功能",
}


class ReadingNavigation:
    def upgrade_search_structure(self):
        if self.index_running or not self.settings.database.is_file():
            return
        settings = self.settings
        self.index_running = True
        self.build_button.setEnabled(False)

        def work(progress):
            with ManualIndex(settings.database, settings.root) as index:
                if index.stats()["documents"] == 0 or StructureIndex(index).ready():
                    return None
                progress({"processed": 0, "total": index.stats()["documents"]})
                return StructureIndex(index).build(progress=progress, backup=True)

        def done(report):
            if report and not report["errors"] and self.settings.database == settings.database:
                if self.query.text().strip():
                    self.search()
            elif report and report["errors"]:
                self.show_error("搜索结构整理未完成，请更新文档索引")

        def finished():
            self.index_running = False
            self.build_button.setEnabled(True)
            self.statusBar().clear_source("structure")

        self.submit(
            work,
            done,
            progress=lambda value: self.statusBar().publish(
                "structure",
                f"整理目录与术语 {value['processed']} / {value['total']}",
                priority=10,
                timeout=0,
            ),
            finished=finished,
        )

    @staticmethod
    def navigation_data(settings, path, preferred):
        with ManualIndex(settings.database, settings.root) as index:
            structure = StructureIndex(index)
            if not structure.ready():
                return None
            positions = structure.placements(path, [preferred.get("manual")] if preferred else [])
            selected = next(
                (row for row in positions if preferred and row["id"] == preferred.get("id")), None
            )
            selected = selected or (positions[0] if positions else None)
            manual = (
                selected["manual"]
                if selected
                else (preferred or {}).get("manual", path.split("/")[1] if "/" in path else "root")
            )
            rows = [
                dict(row)
                for row in index.connection.execute(
                    "SELECT * FROM manual_placements WHERE manual=? ORDER BY position", (manual,)
                )
            ]
            return {
                "manual": manual,
                "selected": selected,
                "rows": rows,
                "positions": positions,
                "related": structure.related(path),
            }

    def populate_manual_navigation(self, item, rows):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        signature = [row["id"] + "\n" + row["title"] + "\n" + row["parent"] for row in rows]
        if data.get("structure_rows") == signature:
            nodes, pending = {}, [item.child(i) for i in range(item.childCount())]
            while pending:
                child = pending.pop()
                value = child.data(0, Qt.ItemDataRole.UserRole) or {}
                if value.get("placement"):
                    nodes[value["placement"]["id"]] = child
                pending.extend(child.child(i) for i in range(child.childCount()))
            return nodes
        item.takeChildren()
        data["loaded"] = True
        data["structure_rows"] = signature
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        nodes = {}
        for row in rows:
            title = chinese_label(row["title"]) or LABELS.get(row["title"], row["title"])
            child = QTreeWidgetItem([title])
            child.setToolTip(0, row["title"])
            child.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {
                    "path": row["path"],
                    "fragment": row["fragment"],
                    "manual": row["manual"],
                    "placement": row,
                },
            )
            nodes.get(row["parent"], item).addChild(child)
            nodes[row["id"]] = child
        return nodes

    def apply_reading_navigation(self):
        if getattr(self, "navigation_applied", None) == self.navigation_generation:
            return
        self.navigation_applied = self.navigation_generation
        navigation = getattr(self, "pending_navigation", None)
        if not navigation or not self.current_document:
            self.breadcrumb.clear()
            return
        self.current_navigation = navigation
        item = self.module_items.get(navigation["manual"])
        selected = navigation["selected"]
        if item:
            self.tree.blockSignals(True)
            try:
                nodes = self.populate_manual_navigation(item, navigation["rows"])
                target = nodes.get(selected["id"]) if selected else item
                ancestor = target
                while ancestor:
                    ancestor.setExpanded(True)
                    ancestor = ancestor.parent()
                self.tree.setCurrentItem(target)
                self.tree.scrollToItem(target)
            finally:
                self.tree.blockSignals(False)
            self.tabs.setCurrentWidget(self.tree)
        if self.history.current:
            self.history.current.placement = selected or {"manual": navigation["manual"]}
        manual = self.catalog.manuals.get(navigation["manual"])
        by_id = {row["id"]: row for row in navigation["rows"]}
        trail, row = [], selected
        while row:
            trail.append(row)
            row = by_id.get(row["parent"])
        trail.reverse()
        self.breadcrumb_targets = (
            [{"path": manual.entry, "manual": manual.id}] if manual else []
        ) + trail
        labels = ([manual.label] if manual else []) + [
            chinese_label(row["title"]) or LABELS.get(row["title"], row["title"]) for row in trail
        ]
        self.breadcrumb.setText(
            " › ".join(f'<a href="{i}">{html.escape(label)}</a>' for i, label in enumerate(labels))
            or "未编入功能目录"
        )
        self.breadcrumb.setToolTip(" / ".join(labels))
        self.return_results.setVisible(self.results.count() > 0)
        self.other_positions.setVisible(len(navigation["positions"]) > 1)
        self.reference_reading.setVisible(bool(navigation["related"]))

    def breadcrumb_clicked(self, value):
        try:
            row = self.breadcrumb_targets[int(value)]
        except (ValueError, IndexError):
            return
        self.open_document(row["path"], row.get("fragment", ""), placement=row)

    def choose_other_position(self):
        menu = QMenu(self)
        for row in self.current_navigation["positions"]:
            manual = self.catalog.manuals.get(row["manual"])
            action = menu.addAction(manual.label if manual else row["manual"])
            action.triggered.connect(
                lambda checked=False, target=row: self.open_document(
                    target["path"],
                    target["fragment"],
                    placement=target,
                    source_location=self.history.current.source if self.history.current else None,
                )
            )
        menu.exec(self.other_positions.mapToGlobal(self.other_positions.rect().bottomLeft()))

    def open_reference_reading(self):
        menu = QMenu(self)
        for row in self.current_navigation["related"]:
            action = menu.addAction(chinese_label(row["title"]) or row["title"])
            action.triggered.connect(
                lambda checked=False, target=row: self.open_source(
                    target["source"], placement={"manual": self.current_navigation["manual"]}
                )
            )
        menu.exec(self.reference_reading.mapToGlobal(self.reference_reading.rect().bottomLeft()))
