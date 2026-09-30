import sys

from loguru import logger
from tqdm import tqdm

_console_sink_id = None


def _console_sink(message):
    # The standalone exe starts without stderr and swaps in devnull later, so
    # look the stream up per message instead of binding it at import time.
    stream = sys.stderr
    if stream is None:
        return
    tqdm.write(message.rstrip(), file=stream)
    stream.flush()


def _is_tty(stream):
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def set_console_level(level="INFO"):
    """Reinstall the console sink; loguru cannot change a sink's level in place."""
    global _console_sink_id
    if _console_sink_id is not None:
        logger.remove(_console_sink_id)
    # Piped hosts (Tauri backend.log, tests) must not receive ANSI colors.
    _console_sink_id = logger.add(_console_sink, level=level, colorize=_is_tty(sys.stderr), enqueue=True)


logger.remove()
set_console_level()
# Without retention loguru keeps every rotated file forever.
logger.add("chaoxing.log", rotation="10 MB", retention=5, level="TRACE")
