import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure structured (JSON-line) logging to stdout.

    Idempotent: safe to call multiple times (e.g. once from app.py's startup
    event and once from a standalone CLI entrypoint) without adding duplicate handlers.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format=(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"module":"%(name)s","msg":"%(message)s"}'
        ),
        stream=sys.stdout,
    )
    _CONFIGURED = True
