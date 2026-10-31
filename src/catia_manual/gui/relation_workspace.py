"""Page-relationship workspace, evidence navigation and independent view persistence."""

import json
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu

from catia_manual.core.index import ManualIndex
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.navigation import Location
from catia_manual.core.relations import RELATION_LABELS, RelationService, relation_request
from catia_manual.gui.image_preview import ImagePreview


class RelationWorkspace:
    def init_relations(self):
        self.pending_relation_save = None
        self.last_relation_save = None
        self.relation_save_timer = QTimer(self)
        self.relation_save_timer.setSingleShot(True)
        self.relation_save_timer.timeout.connect(self.flush_relation_state)

    def relation_active(self):
        return self.center_stack.currentWidget() == self.relation_graph

    def relation_focus(self):
        if self.relation_active() and self.relation_graph.snapshot:
            request = self.relation_graph.snapshot.request
            return {"path": request["path"], "manual": request["manual"]}
        return None

    def relation_state(self):
        snapshot = self.relation_graph.snapshot
        return (
            {
                "schema_version": 1,
                "library_id": snapshot.library_id,
                "graph_revision": snapshot.graph_revision,
                "request": deepcopy(snapshot.request),
                **self.relation_graph.state(),
            }
            if snapshot
            else None
        )

    def save_relation_location(self):
        if self.graph_changing:
            return
        if (
            not self.relation_active()
            or not self.history.current
            or self.history.current.path != "@relations"
        ):
            return
        state = self.relation_state()
        self.history.current.relation_state = state
        identity = (str(self.settings.database), json.dumps(state, sort_keys=True))
        if identity != self.last_relation_save:
            self.last_relation_save = identity
            self.pending_relation_save = (self.settings, state, self.catalog)
            self.relation_save_timer.start(700)

    def flush_relation_state(self):
        self.relation_save_timer.stop()
        pending, self.pending_relation_save = self.pending_relation_save, None
        if pending:
            settings, state, catalog = pending

            def work(_):
                with ManualIndex(settings.database, settings.root) as index:
                    RelationService(index, catalog).save_view(state)

            self.submit(work, lambda _: None)

    def toggle_relations(self, checked):
        if checked:
            self.show_relation_graph()
        elif self.relation_active():
            self.go_back() if self.history.can_back else self.show_home()

    def show_relation_graph(self, *, request=None, state=None, history_index=None, refresh=False):
        self.save_location_scroll()
        if request is None:
            if self.relation_active():
                state = state or self.relation_state()
                request = state["request"]
            else:
                focus = self.reading_map_focus()
                if not focus and self.map_active() and self.function_map.snapshot:
                    item = self.function_map.items.get(self.function_map.selected_id())
                    if item and item.node["document_ref"]:
                        focus = {
                            "path": item.node["document_ref"]["path"],
                            "manual": (item.node["placement_context"] or {}).get("manual", ""),
                        }
                if not focus and self.current_document:
                    focus = {"path": self.current_document.path}
                if not focus:
                    self.relation_button.setChecked(False)
                    return
                request = focus
        request = relation_request(request)
        self.detach_reading_page()
        self.invalidate_translation()
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.page_loading = True
        self.expected_render = None
        self.requested_history_position = history_index
        settings, catalog = self.settings, self.catalog
        self.statusBar().publish("relations", "加载关系…", priority=40, timeout=0)

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
                service = RelationService(index, catalog)
                restored = state
                query = deepcopy(request)
                if restored is None and not refresh:
                    restored = service.load_view(query["path"])
                if restored:
                    query = deepcopy(restored["request"])
                    query["manual"] = request["manual"]
                snapshot = service.map(query)
                if restored and restored.get("graph_revision") != snapshot.graph_revision:
                    query = {
                        "path": request["path"],
                        "manual": request["manual"],
                        "types": query["types"],
                    }
                    snapshot, restored = service.map(query), None
                return snapshot, restored

        def loaded(value):
            if generation != self.navigation_generation:
                return
            snapshot, restored = value
            self.page_loading = False
            self.requested_history_position = None
            with self.graph_transition():
                self.center_stack.setCurrentWidget(self.relation_graph)
                self.map_chrome(True)
                self.relation_graph.set_snapshot(
                    snapshot, restored, reading={"path": request["path"]}
                )
                if history_index is not None:
                    self.history.jump(history_index)
                elif not refresh:
                    self.history.visit(
                        Location(
                            "@relations", title="页面关系图", relation_state=self.relation_state()
                        )
                    )
            self.update_history_actions()
            self.save_relation_location()
            self.statusBar().clear_source("relations")

        def failed(message):
            if generation != self.navigation_generation:
                return
            self.page_loading = False
            self.requested_history_position = None
            self.map_chrome(self.map_active())
            self.statusBar().clear_source("relations")
            if self.center_stack.currentWidget() == self.web and self.reading_page:
                self.attach_reading_page()
            self.show_error(message)

        self.submit(work, loaded, error=failed)

    def refresh_relations(self, request):
        state = self.relation_state()
        state["request"] = deepcopy(request)
        self.show_relation_graph(request=request, state=state, refresh=True)

    def relation_toggle_node(self, identity):
        request = deepcopy(self.relation_graph.snapshot.request)
        if identity == self.relation_graph.snapshot.root:
            return
        expanded = set(request["expanded"])
        expanded.discard(identity) if identity in expanded else expanded.add(identity)
        request["expanded"] = sorted(expanded)
        self.refresh_relations(request)

    def relation_page_node(self, identity, direction):
        node = self.relation_graph.items[identity].node
        request = deepcopy(self.relation_graph.snapshot.request)
        step = node["page_size"] if direction > 0 else 20
        request["offsets"][identity] = max(0, node["offset"] + direction * step)
        self.refresh_relations(request)

    def relation_types(self, types):
        if self.relation_graph.snapshot:
            request = deepcopy(self.relation_graph.snapshot.request)
            request["types"], request["offsets"] = types, {}
            self.refresh_relations(request)

    def relation_recenter(self, identity):
        node = self.relation_graph.items[identity].node
        if node["kind"] == "article" and node["exists"]:
            self.show_relation_graph(
                request={
                    "path": node["document_ref"]["path"],
                    "manual": (node["placement_context"] or {}).get("manual", ""),
                }
            )

    def relation_proofs(self, identity):
        result, seen = [], set()
        for edge in self.relation_graph.snapshot.edges:
            if identity not in {edge["source"], edge["target"]}:
                continue
            for proof in edge["evidence"]:
                key = json.dumps(proof, sort_keys=True)
                if key not in seen:
                    result.append((edge, proof))
                    seen.add(key)
        return result

    def relation_can_open(self, node):
        if not node["exists"]:
            return False
        if node["kind"] == "sample":
            self.refresh_capabilities()
            return self.capabilities.sample_launch.available
        return True

    def open_relation_node(self, identity):
        node = self.relation_graph.items[identity].node
        if not self.relation_can_open(node):
            return
        if node["kind"] == "article":
            source = node["source"]
            proofs = self.relation_proofs(identity)
            incoming = next(
                (p for e, p in proofs if e["target"] == identity and p.get("fragment")), None
            )
            if source and incoming:
                source = replace(
                    SourceLocation.from_dict(source), fragment=incoming["fragment"]
                ).to_dict()
            if source:
                self.open_source(source, placement=node["placement_context"])
            else:
                self.open_document(
                    node["document_ref"]["path"],
                    incoming["fragment"] if incoming else node["fragment"],
                    placement=node["placement_context"],
                )
        elif node["kind"] == "sample":
            self.launch_sample(node["resource_ref"]["path"])
        elif node["kind"] == "image":
            try:
                paths = [
                    n["resource_ref"]["path"]
                    for n in self.relation_graph.snapshot.nodes
                    if n["kind"] == "image" and n["exists"]
                ]
                preview = ImagePreview(self.resources, paths, node["resource_ref"]["path"], self)
                self.image_windows.add(preview)
                preview.destroyed.connect(lambda: self.image_windows.discard(preview))
                preview.show()
            except (ValueError, OSError) as exc:
                self.show_error(str(exc))
        else:
            self.relation_sources()

    def relation_sources(self):
        identity = self.relation_graph.selected_id()
        if not identity:
            return
        proofs = self.relation_proofs(identity)
        menu = QMenu(self)
        for edge, proof in proofs[:40]:
            source = proof["source"]
            label = proof.get("article_title") or source["document"]["path"].rsplit("/", 1)[-1]
            label = RELATION_LABELS[edge["kind"]] + " · " + label
            if proof.get("element"):
                label += f" · 第 {proof['element']['occurrence'] + 1} 处"
            action = menu.addAction(label[:110])
            action.setToolTip(source["text_quote"] or proof["label"] or source["fragment"])
            action.triggered.connect(lambda checked=False, p=proof: self.open_relation_proof(p))
        if len(proofs) > 40:
            menu.addAction(f"共 {len(proofs)} 处").setEnabled(False)
        if not proofs:
            node = self.relation_graph.items[identity].node
            if node["source"]:
                menu.addAction("页面开头").triggered.connect(
                    lambda: self.open_source(node["source"])
                )
        menu.exec(
            self.relation_graph.source_button.mapToGlobal(
                self.relation_graph.source_button.rect().bottomLeft()
            )
        )
        menu.deleteLater()

    def open_relation_proof(self, proof):
        self.open_source(
            proof["source"],
            placement={"manual": self.relation_graph.snapshot.request["manual"]},
            element_location=proof.get("element"),
        )

    def relation_context_menu(self, position):
        item = self.relation_graph.view.itemAt(position)
        if not item or not hasattr(item, "node"):
            return
        node = item.node
        self.relation_graph.select(node["id"])
        menu = QMenu(self)
        menu.addAction("原文出处…").triggered.connect(self.relation_sources)
        if node["kind"] == "article":
            menu.addAction("打开文章").triggered.connect(
                lambda: self.open_relation_node(node["id"])
            )
            self.add_original_action(menu, node["document_ref"]["path"], node["fragment"])
            menu.addAction("以此页面为中心").triggered.connect(
                lambda: self.relation_recenter(node["id"])
            )
        elif node["resource_ref"]:
            self.add_resource_actions(
                menu,
                node["resource_ref"]["path"],
                image=node["kind"] == "image",
                preview=lambda _: self.open_relation_node(node["id"]),
            )
        if node["children"] and node["id"] != self.relation_graph.snapshot.root:
            menu.addAction("展开 / 收起").triggered.connect(
                lambda: self.relation_toggle_node(node["id"])
            )
        menu.exec(self.relation_graph.view.viewport().mapToGlobal(position))
        menu.deleteLater()

    def restore_relation_element(self):
        element = getattr(self, "pending_relation_element", None)
        self.pending_relation_element = None
        if not element or element.get("kind") != "image":
            return
        path, occurrence = element.get("path"), element.get("occurrence", 0)
        if not isinstance(path, str) or type(occurrence) is not int or not 0 <= occurrence < 100000:
            return
        script = (
            "(()=>{const nodes=[...document.querySelectorAll('img[data-cmv-image]')]"
            ".filter(n=>n.dataset.cmvImage==="
            + json.dumps(path)
            + ");const node=nodes["
            + str(occurrence)
            + "];if(node){node.scrollIntoView({block:'center'});return true;}return false;})()"
        )
        generation = self.navigation_generation
        self.application_script(
            script,
            lambda found: (
                self.show_error("图片原文位置已变化")
                if not found and generation == self.navigation_generation
                else None
            ),
        )
