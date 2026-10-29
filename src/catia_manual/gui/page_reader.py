"""Qt adapter for page translation. Core tasks/cache know nothing about widgets or DOM."""

import hashlib
import json
from collections import OrderedDict

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QToolButton, QWidget

from catia_manual.core.index import ManualIndex
from catia_manual.core.page_translation import PageTranslationService, PageTranslationTask
from catia_manual.core.providers import PROVIDERS
from catia_manual.core.translation import make_provider
from catia_manual.gui.style import ASSETS

READER_SCRIPT = (ASSETS / "reader_translation.js").read_text("utf-8")


class PageReader:
    def init_page_reader(self):
        self.page_task = None
        self.reading_page = None
        self.page_ready = False
        self.page_worker_running = False
        self.page_sessions = OrderedDict()
        self.reading_positions = {}
        self.page_service_error = ""
        self.page_pump = QTimer(self)
        self.page_pump.setSingleShot(True)
        self.page_pump.timeout.connect(self.pump_page_translation)
        self.position_timer = QTimer(self)
        self.position_timer.setSingleShot(True)
        self.position_timer.timeout.connect(self.capture_reading_position)

    def add_page_controls(self, nav):
        self.reading_controls = QWidget()
        row = QHBoxLayout(self.reading_controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, label in (("original", "原文"), ("bilingual", "双语"), ("chinese", "中文")):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setObjectName("readingMode")
            button.clicked.connect(lambda checked=False, name=mode: self.set_reading_mode(name))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            row.addWidget(button)
        self.pause_page_button = QToolButton()
        self.pause_page_button.clicked.connect(self.toggle_page_pause)
        row.addWidget(self.pause_page_button)
        self.retry_page_button = QToolButton()
        self.retry_page_button.setText("重试")
        self.retry_page_button.clicked.connect(lambda: self.retry_page_translation())
        row.addWidget(self.retry_page_button)
        nav.addWidget(self.reading_controls)
        self.reading_controls.hide()
        self.page_progress = QLabel()
        self.page_progress.setMaximumWidth(350)
        self.statusBar().addPermanentWidget(self.page_progress)

    def detach_reading_page(self):
        self.capture_reading_position()
        self.page_pump.stop()
        self.page_task = None
        self.page_ready = False
        self.reading_controls.hide()
        self.page_progress.clear()

    def capture_reading_position(self):
        task = self.page_task
        location = self.history.current
        if not task or self.page_loading or not self.page_ready:
            return

        def captured(value):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    return
            if isinstance(value, dict):
                self.reading_positions[task.page.key] = value
                if location and location.path == task.page.path:
                    location.reading_anchor = value

        self.application_script(
            "JSON.stringify(window.cmvReader && window.cmvReader.capture())", captured
        )

    def attach_reading_page(self, restore=None, fragment=""):
        page = self.reading_page
        if page is None:
            return
        config = dict(self.settings.translation)
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        identity = (str(self.settings.database), page.key, fingerprint)
        task = self.page_sessions.get(identity)
        fresh = task is None
        if fresh:
            task = PageTranslationTask(page)
            self.page_sessions[identity] = task
        self.page_sessions.move_to_end(identity)
        while len(self.page_sessions) > 20:
            self.page_sessions.popitem(last=False)
        self.page_task = task
        self.page_ready = False
        self.page_service_error = ""
        self.reading_controls.show()
        provider_label = PROVIDERS.get(config.get("provider"), {}).get("label", "翻译服务")
        characters = sum(len(unit.text) for unit in page.units)
        for mode in ("bilingual", "chinese"):
            self.mode_buttons[mode].setToolTip(
                f"使用 {provider_label} 翻译当前页，约 {characters} 字符；已缓存段落直接复用。"
            )
        self.update_page_controls()
        generation = self.navigation_generation
        settings = self.settings
        position = (
            restore if restore is not None else ({"fragment": fragment} if fragment else None)
        )
        if position is None:
            position = self.reading_positions.get(page.key)

        def cache_work(_):
            with ManualIndex(settings.database, settings.root) as index:
                service = PageTranslationService(
                    index, make_provider(config), page, config.get("target_language", "zh-CN")
                )
                return service.cached()

        def loaded(results):
            if self.page_task is not task or self.navigation_generation != generation:
                return
            task.results.update(results)
            self.page_ready = True
            self.apply_page_update(position=position)
            self.update_page_controls()
            self.page_pump.start(100)

        def failed(message):
            if self.page_task is task and self.navigation_generation == generation:
                self.page_ready = True
                self.page_service_error = message
                self.apply_page_update(position=position)
                self.update_page_controls()

        def installed(_):
            if self.page_task is task and self.navigation_generation == generation:
                self.submit(cache_work, loaded, error=failed)

        self.application_script(READER_SCRIPT + "\nBoolean(window.cmvReader)", installed)

    def apply_page_update(self, position=None, results=None):
        task = self.page_task
        if not task or not self.page_ready:
            return
        payload = {
            "mode": task.mode,
            "results": {
                k: v.text for k, v in (task.results if results is None else results).items()
            },
            "errors": task.errors,
        }
        if position is not None:
            payload["position"] = position
        self.application_script(
            "window.cmvReader && window.cmvReader.apply("
            + json.dumps(task.page.key)
            + ","
            + json.dumps(payload)
            + ")"
        )

    def set_reading_mode(self, mode):
        if not self.page_task:
            return
        self.page_task.mode = mode
        self.apply_page_update(results={})
        self.update_page_controls()
        self.page_pump.start(100)

    def toggle_page_pause(self):
        if self.page_task:
            self.page_task.paused = not self.page_task.paused
            self.page_task.consecutive_failures = 0
            self.update_page_controls()
            self.page_pump.start(100)

    def retry_page_translation(self, identity=None):
        task = self.page_task
        if not task:
            return
        if identity is not None and identity not in {unit.id for unit in task.page.units}:
            return
        task.retry(identity)
        if self.page_service_error:
            self.attach_reading_page()
        else:
            self.apply_page_update(results={})
            self.update_page_controls()
            self.page_pump.start(100)

    def update_page_controls(self):
        task = self.page_task
        if task is None:
            return
        self.mode_buttons[task.mode].setChecked(True)
        active = task.mode != "original"
        pending = len(task.results) + len(task.errors) < len(task.page.units)
        self.pause_page_button.setVisible(active and pending and not self.page_service_error)
        self.pause_page_button.setText("继续" if task.paused else "暂停")
        self.pause_page_button.setToolTip("暂停后不再发送新段落，正在处理的段落仍会缓存")
        self.retry_page_button.setVisible(active and bool(task.errors or self.page_service_error))
        if not active:
            self.page_progress.clear()
            return
        if self.page_service_error:
            self.page_progress.setText("整页翻译未就绪 · 请检查翻译设置")
            self.page_progress.setToolTip(self.page_service_error)
            return
        state = "已暂停" if task.paused else ("翻译中" if pending else "完成")
        if task.errors:
            state = state if pending or task.paused else "部分完成"
            state += f" · {len(task.errors)} 段未完成"
        cached = sum(result.cached for result in task.results.values())
        self.page_progress.setText(f"本页 {len(task.results)}/{len(task.page.units)} · {state}")
        self.page_progress.setToolTip(
            f"{cached} 段来自缓存\n" + "\n".join(task.errors.values())[:1200]
        )

    def pump_page_translation(self):
        task = self.page_task
        if (
            not task
            or not self.page_ready
            or self.page_loading
            or self.page_worker_running
            or self.page_service_error
            or self.translation_running
            or self.translation_pending is not None
            or self.selection_timer.isActive()
        ):
            return
        if not task.next_unit():
            self.update_page_controls()
            return

        def visible_ready(visible):
            if isinstance(visible, str):
                try:
                    visible = json.loads(visible)
                except ValueError:
                    visible = []
            if self.page_task is not task or self.page_loading or self.page_worker_running:
                return
            # Recheck selection priority after the asynchronous viewport query.
            if (
                self.translation_running
                or self.translation_pending is not None
                or self.selection_timer.isActive()
            ):
                return
            unit = task.next_unit(visible if isinstance(visible, list) else ())
            if unit is None:
                return
            self.page_worker_running = True
            settings, config = self.settings, dict(self.settings.translation)

            def work(_):
                with ManualIndex(settings.database, settings.root) as index:
                    service = PageTranslationService(
                        index,
                        make_provider(config),
                        task.page,
                        config.get("target_language", "zh-CN"),
                    )
                    return service.translate(unit)

            def done(result):
                task.succeed(unit.id, result)
                if self.page_task is task and not self.page_loading:
                    self.apply_page_update(results={unit.id: result})
                    self.update_page_controls()

            def failed(message):
                task.fail(unit.id, message)
                if self.page_task is task and not self.page_loading:
                    self.apply_page_update(results={})
                    self.update_page_controls()

            def finished():
                self.page_worker_running = False
                self.start_translation()
                self.page_pump.start(100)

            self.submit(work, done, finished=finished, error=failed)

        self.application_script(
            "JSON.stringify(window.cmvReader ? window.cmvReader.visible() : [])", visible_ready
        )
