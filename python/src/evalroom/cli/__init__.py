"""The ``evalroom`` command line. See :func:`evalroom.cli.main.main`."""

from __future__ import annotations

import sys

from .main import CLI_VERSION, EXIT, CliIO, main

__all__ = ["CLI_VERSION", "EXIT", "CliIO", "console_main", "main"]


def console_main() -> None:
    """Entry point of the ``evalroom`` console script."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        code = main(sys.argv[1:])
    except KeyboardInterrupt:
        code = 130
    except Exception as error:
        sys.stderr.write(f"evalroom: unexpected error: {error!r}\n")
        code = 70
    sys.stdout.flush()
    raise SystemExit(code)
