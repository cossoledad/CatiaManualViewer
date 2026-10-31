"""Portable dispatcher: backend mode must not import Qt or start a GUI instance."""

import sys


def main():
    if sys.argv[1:2] == ["--agent-service"]:
        from catia_manual.agent.server import main as serve

        return serve(sys.argv[2:])
    from catia_manual.gui.app import main as gui

    return gui()


if __name__ == "__main__":
    raise SystemExit(main())
