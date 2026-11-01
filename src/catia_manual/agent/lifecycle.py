"""Small bootstrap reports remain usable even when the MCP SDK cannot import."""

import json
from pathlib import Path

FAILURES = {
    "PORT_IN_USE": "端口已被占用，请更换端口或停止占用进程",
    "ACCESS_DENIED": "系统拒绝监听，请检查端口限制及软件访问权限",
    "ADDRESS_UNAVAILABLE": "监听地址不可用，请重新选择监听范围",
    "DEPENDENCY_MISSING": "Agent 运行依赖缺失，请完整解压便携版",
    "STARTUP_ERROR": "后台初始化失败，请查看调用日志中的错误类型",
    "PROCESS_START_FAILED": "无法启动后台程序，请检查程序路径及访问权限",
    "PROCESS_CRASHED": "后台进程异常退出，请查看调用日志",
    "STARTUP_TIMEOUT": "后台启动超时，请查看调用日志后重试",
}


def failure(exc):
    number = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    code = {
        10048: "PORT_IN_USE",
        98: "PORT_IN_USE",
        10013: "ACCESS_DENIED",
        13: "ACCESS_DENIED",
        10049: "ADDRESS_UNAVAILABLE",
        99: "ADDRESS_UNAVAILABLE",
    }.get(number, "DEPENDENCY_MISSING" if isinstance(exc, ImportError) else "STARTUP_ERROR")
    # Only identifiers/numbers; exception messages may contain configuration secrets.
    return {"code": code, "exception": type(exc).__name__, "os_error": number}


def write_runtime(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), "utf-8")
    temporary.replace(path)


def failed_runtime(argv, instance, exc):
    try:
        path = argv[argv.index("--runtime") + 1]
        write_runtime(path, {"instance": instance, "state": "failed", **failure(exc)})
    except (OSError, ValueError, IndexError):
        pass
