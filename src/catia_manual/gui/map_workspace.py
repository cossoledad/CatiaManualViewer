"""Functional-map workspace/history adapter; all hierarchy queries run off the GUI thread."""

import json
from contextlib import contextmanager
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMenu

from catia_manual.core.function_map import MAP_VERSION, PAGE_SIZE, GraphService, map_request
from catia_manual.core.index import ManualIndex
from catia_manual.core.navigation import Location


class MapWorkspace:
    def init_function_map(self):
        self.map_generation = 0
        self.graph_changing = False
        self.pending_map_save = None
        self.last_map_save = None
        self.map_save_timer = QTimer(self)
        self.map_save_timer.setSingleShot(True)
        self.map_save_timer.timeout.connect(self.flush_map_state)

    @contextmanager
    def graph_transition(self):
        self.graph_changing = True
        try:
            yield
        finally:
            self.graph_changing = False

    def map_active(self):
        return self.center_stack.currentWidget() in (self.function_map, self.relation_graph)

    def map_chrome(self, active):
        self.map_button.blockSignals(True)
        relation = active and self.relation_active()
        self.map_button.setChecked(active and not relation)
        self.map_button.blockSignals(False)
        self.relation_button.setChecked(relation)
        self.relation_button.setEnabled(bool(self.current_document or active))
        self.reading_controls.setVisible(not active)
        self.focus_button.setVisible(not active)
        self.tools_button.setEnabled(not active)
        self.tools_button.setVisible(not active)
        self.breadcrumb.setVisible(not active)
        self.return_results.setVisible(not active and self.results.count() > 0)
        self.details_pane.setVisible(
            not active and self.settings.appearance.get("show_tools", True)
        )
        if active:
            self.breadcrumb.clear()
            self.reference_reading.hide()
            self.other_positions.hide()
            self.page_title.setText("页面关系图" if relation else "功能地图")

    def toggle_function_map(self, enabled):
        if enabled:
            self.show_function_map()
        elif self.map_active():
            if self.history.can_back:
                self.go_back()
            else:
                self.show_home()

    def reading_map_focus(self):
        current = self.history.current
        if current and not current.path.startswith("@"):
            placement = current.placement or {}
            return {
                "path": current.path,
                "manual": placement.get("manual", ""),
                "fragment": current.fragment,
                "trail": placement.get("trail", []),
            }
        return None

    def map_state(self):
        snapshot = self.function_map.snapshot
        if snapshot is None:
            return None
        return {
            "schema_version": MAP_VERSION,
            "library_id": snapshot.library_id,
            "graph_revision": snapshot.graph_revision,
            "request": deepcopy(snapshot.request),
            **self.function_map.state(),
            "search_terms": list(getattr(self, "map_search_terms", [])),
        }

    def save_map_location(self):
        if self.graph_changing:
            return
        if self.relation_active():
            self.save_relation_location()
            return
        if not self.map_active():
            return
        state = self.map_state()
        if state and self.history.current and self.history.current.path == "@map":
            self.history.current.map_state = state
            identity = (str(self.settings.database), json.dumps(state, sort_keys=True))
            if self.last_map_save == identity:
                return
            self.last_map_save = identity
            self.pending_map_save = (self.settings, state)
            self.map_save_timer.start(700)

    def flush_map_state(self):
        self.flush_relation_state()
        self.map_save_timer.stop()
        pending, self.pending_map_save = self.pending_map_save, None
        if pending:
            settings, state = pending
            catalog = self.catalog

            def work(_):
                with ManualIndex(settings.database, settings.root) as index:
                    GraphService(index, catalog).save_view(state)

            self.submit(work, lambda _: None)

    def show_function_map(
        self,
        *,
        request=None,
        state=None,
        history_index=None,
        refresh=False,
        reveal=True,
        terms=None,
    ):
        self.save_location_scroll()
        focus = self.reading_map_focus() or self.relation_focus()
        reading = focus
        if not reading and self.current_document:
            placement = (getattr(self, "current_navigation", None) or {}).get("selected") or {}
            reading = {"path": self.current_document.path, "manual": placement.get("manual", "")}
        previous_map = self.center_stack.currentWidget() == self.function_map
        if request is None:
            if previous_map:
                request = deepcopy(self.function_map.snapshot.request)
                state = state or self.map_state()
            else:
                scope = deepcopy(self.module.currentData() or {"kind": "all"})
                if focus and focus.get("manual") in self.catalog.manuals:
                    scope = {"kind": "manual", "id": focus["manual"]}
                request = {"scope": scope}
                if focus:
                    request["focus"] = focus
        request = map_request(request)
        self.detach_reading_page()
        self.invalidate_translation()
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.page_loading = True
        self.expected_render = None
        self.requested_history_position = history_index
        settings, catalog = self.settings, self.catalog
        if terms is None:
            terms = (
                state.get("search_terms", [])
                if state
                else ((self.history.current.search_terms or []) if self.history.current else [])
            )
        self.statusBar().publish("map", "加载地图…", priority=40, timeout=0)

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
                service = GraphService(index, catalog)
                view_state = state
                query = deepcopy(request)
                if view_state is None and not refresh and not query.get("focus"):
                    stored = service.load_view(query["scope"])
                    if stored and stored["request"].get("focus") == query.get("focus"):
                        view_state = stored
                if view_state and view_state.get("graph_revision") == service.revision:
                    query = deepcopy(view_state["request"])
                elif view_state:
                    query.pop("expanded", None)
                    query["offsets"] = {}
                    view_state = None
                return service.map(query, reveal=reveal), view_state

        def loaded(result):
            if generation != self.navigation_generation:
                return
            snapshot, restored = result
            self.page_loading = False
            self.requested_history_position = None
            with self.graph_transition():
                self.center_stack.setCurrentWidget(self.function_map)
                self.map_chrome(True)
                self.map_search_terms = list(terms)
                self.function_map.set_snapshot(snapshot, restored, terms=terms, reading=reading)
                if history_index is not None:
                    self.history.jump(history_index)
                elif not refresh:
                    self.history.visit(
                        Location("@map", title="功能地图", map_state=self.map_state())
                    )
            self.return_results.hide()
            self.update_history_actions()
            self.save_map_location()
            self.statusBar().clear_source("map")

        def failed(message):
            if generation != self.navigation_generation:
                return
            self.page_loading = False
            self.requested_history_position = None
            self.map_button.setChecked(previous_map)
            self.statusBar().clear_source("map")
            if self.center_stack.currentWidget() == self.web and self.reading_page:
                self.attach_reading_page()
            self.show_error(message)

        self.submit(work, loaded, error=failed)

    def refresh_function_map(self, request, *, reveal=False, keep_view=True):
        state = self.map_state() if keep_view else None
        if state:
            state["request"] = deepcopy(request)
        self.show_function_map(request=request, state=state, refresh=True, reveal=reveal)

    def map_toggle_node(self, identity):
        node = self.function_map.items.get(identity)
        if not node or not node.node["children"]:
            return
        request = deepcopy(self.function_map.snapshot.request)
        expanded = set(request.get("expanded", []))
        if identity in expanded:
            expanded.remove(identity)
        else:
            expanded.add(identity)
        request["expanded"] = sorted(expanded)
        self.refresh_function_map(request)

    def map_page_node(self, identity, direction):
        item = self.function_map.items.get(identity)
        if not item:
            return
        request = deepcopy(self.function_map.snapshot.request)
        step = item.node["page_size"] if direction > 0 else PAGE_SIZE
        request.setdefault("offsets", {})[identity] = max(0, item.node["offset"] + direction * step)
        request["expanded"] = list(set(request.get("expanded", [])) | {identity})
        self.refresh_function_map(request)

    def map_drill_node(self, identity):
        item = self.function_map.items.get(identity)
        if not item or not item.node["document_ref"]:
            return
        request = deepcopy(self.function_map.snapshot.request)
        p = item.node["placement_context"] or {}
        request["branch"] = {
            "path": item.node["document_ref"]["path"],
            "manual": p.get("manual", ""),
            "fragment": item.node["fragment"],
            "trail": p.get("trail", []),
        }
        request.pop("expanded", None)
        request["offsets"] = {}
        self.refresh_function_map(request, reveal=True, keep_view=False)

    def map_up(self):
        request = deepcopy(self.function_map.snapshot.request)
        if request.get("branch"):
            if self.function_map.snapshot.parent:
                request["branch"] = self.function_map.snapshot.parent
            else:
                request.pop("branch")
        elif request["scope"]["kind"] == "manual":
            manual = request["scope"]["id"]
            category = next(
                c for c in self.catalog.categories if any(m.id == manual for m in c.manuals)
            )
            request = {"scope": {"kind": "category", "id": category.id}}
        else:
            request = {"scope": {"kind": "all"}}
        request.pop("expanded", None)
        request["offsets"] = {}
        self.refresh_function_map(request, reveal=True, keep_view=False)

    def map_current(self):
        # The last article remains available while the graph occupies the reading workspace.
        if self.current_document:
            p = (getattr(self, "current_navigation", None) or {}).get("selected") or {}
            request = {
                "scope": {"kind": "manual", "id": p.get("manual", "")},
                "focus": {
                    "path": self.current_document.path,
                    "manual": p.get("manual", ""),
                    "fragment": p.get("fragment", ""),
                    "trail": p.get("trail", []),
                },
            }
            if not request["scope"]["id"]:
                request["scope"] = {"kind": "all"}
            self.refresh_function_map(request, reveal=True, keep_view=False)

    def open_map_node(self, identity):
        item = self.function_map.items.get(identity)
        if not item or not item.node["document_ref"]:
            return
        node = item.node
        if node["source"]:
            self.open_source(
                node["source"],
                placement=node["placement_context"],
                search_terms=list(self.map_search_terms),
            )
        else:
            self.open_document(
                node["document_ref"]["path"],
                node["fragment"],
                placement=node["placement_context"],
                search_terms=list(self.map_search_terms),
            )

    def result_on_map(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or path == "@index":
            return
        p = item.data(Qt.ItemDataRole.UserRole + 2) or {}
        source = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        request = {
            "scope": {"kind": "manual", "id": p["manual"]} if p else {"kind": "all"},
            "focus": {
                "path": path,
                "manual": p.get("manual", ""),
                "fragment": p.get("fragment", source.get("fragment", "")),
                "trail": p.get("trail", []),
            },
        }
        self.show_function_map(request=request, terms=item.data(Qt.ItemDataRole.UserRole + 3) or [])

    def map_context_menu(self, position):
        item = self.function_map.view.itemAt(position)
        if not item or not hasattr(item, "node"):
            return
        self.function_map.select(item.node["id"])
        menu = QMenu(self)
        node = item.node
        if node["document_ref"]:
            menu.addAction("页面关系图").triggered.connect(
                lambda: self.show_relation_graph(
                    request={
                        "path": node["document_ref"]["path"],
                        "manual": (node["placement_context"] or {}).get("manual", ""),
                    }
                )
            )
            menu.addAction("打开文章").triggered.connect(lambda: self.open_map_node(node["id"]))
            self.add_original_action(menu, node["document_ref"]["path"], node["fragment"])
        if node["children"]:
            menu.addAction("展开 / 收起").triggered.connect(
                lambda: self.map_toggle_node(node["id"])
            )
            if node["document_ref"]:
                menu.addAction("聚焦分支").triggered.connect(
                    lambda: self.map_drill_node(node["id"])
                )
        if not menu.isEmpty():
            menu.exec(self.function_map.view.viewport().mapToGlobal(position))
        menu.deleteLater()
