"""Collect task-scoped logs and drain them before callers publish completion."""

from contextlib import contextmanager

from api.logger import logger


@contextmanager
def task_log_sink(store, task_id):
    def capture(message):
        record = message.record
        level = record["level"].name.lower()
        if level in {"critical", "fatal"}:
            level = "error"
        # The loguru patcher (patch_record) has already redacted the rendered
        # message; skip append_log's second full pass.
        store.append_log(task_id, str(message), level=level,
                         timestamp=record["time"].timestamp(), redacted=True)

    with logger.contextualize(task_id=task_id):
        sink_id = logger.add(capture, enqueue=True,
                             filter=lambda record: record["extra"].get("task_id") == task_id)
        try:
            yield
        finally:
            try:
                logger.complete()
            finally:
                logger.remove(sink_id)
