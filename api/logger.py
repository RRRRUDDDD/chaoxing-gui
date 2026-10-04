import os
import sys

from loguru import logger
from tqdm import tqdm

from api.paths import data_dir
from api.privacy import patch_record

_console_sink_id = None

# Full upstream payloads used to reach the file sink at TRACE level and blow
# through the 10 MB rotations; high-volume call sites wrap them in this.
LOG_PAYLOAD_LIMIT = 2048


def truncated(value, limit=LOG_PAYLOAD_LIMIT):
    """Cap a high-volume payload before it reaches the rotating file sink."""
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated, total {len(text)} chars]"


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


logger.configure(patcher=patch_record)
logger.remove()
set_console_level()
# Resolve against the shared data directory so test runs (which import this
# module from the repository root) never create ./chaoxing.log.
logger.add(os.path.join(data_dir(), "chaoxing.log"), rotation="10 MB", retention=5, level="TRACE")
