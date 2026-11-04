"""Bounded local MCP diagnostics; never persist request arguments or source text."""

import ipaddress
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from catia_manual import __version__

TOOLS = {"describe_library", "search", "fetch", "browse", "resolve_source", "resolve_share"}
METHODS = {
    "initialize",
    "notifications/initialized",
    "notifications/cancelled",
    "ping",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/templates/list",
    "prompts/list",
}
ERRORS = {
    "INVALID_ARGUMENT",
    "INVALID_REFERENCE",
    "STALE_REFERENCE",
    "STALE_SOURCE",
    "INDEX_NOT_READY",
    "INDEX_UNAVAILABLE",
    "LIBRARY_CHANGED",
    "SOURCE_NOT_FOUND",
    "SOURCE_TOO_LARGE",
    "UNKNOWN_SCOPE",
    "INTERNAL_ERROR",
    "INVALID_SHARE",
}


def log_directory(settings):
    return settings.database.parent / "logs" / "mcp"


def parsed(body):
    try:
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError, RecursionError):
        return {}


class QuietHandler(RotatingFileHandler):
    def handleError(self, record):
        # Disk errors must neither break queries nor dump payloads to stderr.
        self.failed = True


class CallLog:
    def __init__(self, settings, instance_id, *, max_bytes=2 * 1024 * 1024):
        self.directory = log_directory(settings)
        self.instance_id = instance_id
        self.enabled = settings.agent.get("log_calls", True)
        self.available = False
        self.lock = threading.Lock()
        self.handler = None
        self.path = self.directory / (
            f"mcp-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{os.getpid()}-{instance_id}.jsonl"
        )
        if self.enabled:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.prune()
                self.handler = QuietHandler(
                    self.path, maxBytes=max_bytes, backupCount=2, encoding="utf-8", delay=True
                )
                self.available = True
            except OSError:
                pass

    def prune(self):
        sessions = sorted(
            (
                p
                for p in self.directory.glob("mcp-*.jsonl")
                if re.fullmatch(r"mcp-\d{8}T\d{12}Z-\d+-[0-9a-f]{24}\.jsonl", p.name)
            ),
            key=lambda p: p.name,
            reverse=True,
        )
        for base in sessions[9:]:
            for path in (base, base.with_suffix(".jsonl.1"), base.with_suffix(".jsonl.2")):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def write(self, event, **fields):
        if self.handler is None:
            return
        record = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "version": __version__,
            "instance_id": self.instance_id,
            "event": event,
            **fields,
        }
        with self.lock:
            self.handler.failed = False
            try:
                self.handler.emit(
                    logging.makeLogRecord(
                        {
                            "msg": json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                            "args": (),
                            "levelno": logging.INFO,
                        }
                    )
                )
                self.available = not self.handler.failed
            finally:
                # Separate server instances never retain handles on each other's
                # rotated files; Windows can open/copy the log while serving.
                try:
                    self.handler.close()
                except OSError:
                    self.available = False

    async def request(self, scope, receive, send, dispatch):
        started = time.perf_counter()
        request, response = bytearray(), bytearray()
        status, overflow, exception = 500, False, None

        async def read():
            message = await receive()
            body = message.get("body", b"")
            if message["type"] == "http.request" and len(request) + len(body) <= 65536:
                request.extend(body)
            return message

        async def sent(message):
            nonlocal status, overflow
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if len(response) + len(body) <= 256 * 1024:
                    response.extend(body)
                else:
                    overflow = True
            await send(message)

        try:
            return await dispatch(scope, read, sent)
        except BaseException as exc:
            exception = "cancelled" if "cancel" in type(exc).__name__.lower() else "error"
            raise
        finally:
            data, reply = parsed(request), parsed(response) if not overflow else {}
            method = data.get("method")
            method = method if isinstance(method, str) else None
            params = data.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            name = name if isinstance(name, str) else None
            result = reply.get("result")
            result = result if isinstance(result, dict) else {}
            content = result.get("structuredContent")
            content = content if isinstance(content, dict) else {}
            error = content.get("error")
            error = error if isinstance(error, dict) else {}
            outcome = exception or (
                "error"
                if status >= 500 or result.get("isError") or "error" in reply
                else "rejected"
                if status >= 400
                else "ok"
                if not overflow
                else "unknown"
            )
            try:
                peer = str(ipaddress.ip_address((scope.get("client") or ("", 0))[0]))
            except ValueError:
                peer = "unknown"
            fields = {
                "request_id": os.urandom(8).hex(),
                "client": peer,
                "route": scope.get("path")
                if scope.get("path") in {"/mcp", "/health", "/share"}
                else "other",
                "http_method": scope.get("method")
                if scope.get("method") in {"GET", "POST", "DELETE", "OPTIONS"}
                else "other",
                "http_status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "outcome": outcome,
            }
            if method:
                fields["rpc_method"] = method if method in METHODS else "other"
            if method == "tools/call":
                fields["tool"] = name if name in TOOLS else "unknown"
                if isinstance(content.get("results"), list):
                    fields["result_count"] = len(content["results"])
                if isinstance(content.get("blocks"), list):
                    fields["block_count"] = len(content["blocks"])
                if isinstance(content.get("has_more"), bool):
                    fields["has_more"] = content["has_more"]
                if outcome == "error":
                    code = error.get("code")
                    fields["error_code"] = (
                        code if isinstance(code, str) and code in ERRORS else "TOOL_ERROR"
                    )
            self.write("tool_call" if method == "tools/call" else "http_request", **fields)
