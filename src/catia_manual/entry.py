"""Portable dispatcher: backend mode must not import Qt or start a GUI instance."""

import os
import sys


def main():
    if sys.argv[1:2] == ["--agent-service"]:
        from catia_manual.agent.lifecycle import failed_runtime

        instance = os.environ.get("CMV_AGENT_INSTANCE", "")
        try:
            from catia_manual.agent.server import main as serve

            return serve(sys.argv[2:])
        except Exception as exc:
            failed_runtime(sys.argv[2:], instance, exc)
            return 1
    if sys.argv[1:2] == ["--diagnostics-agent"]:
        from catia_manual.diagnostics import verify_agent_controller

        return verify_agent_controller(sys.argv[2:])
    from catia_manual.gui.app import main as gui

    return gui()


if __name__ == "__main__":
    raise SystemExit(main())
