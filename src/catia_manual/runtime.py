"""Installation paths for the standalone portable build and source development."""

import sys
from pathlib import Path


def is_compiled() -> bool:
    # Nuitka supplies this module attribute to every compiled module.
    return "__compiled__" in globals()


def application_directory() -> Path:
    if is_compiled():
        # Our release format is standalone, so the executable is beside its configuration.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
