"""One place to configure logging for both the orchestrator and worker
processes, so log lines are structured (JSON) and consistently include
correlation fields (worker_id, job_id, task_id) instead of the ad-hoc
print() statements workers used to write to stdout."""
import json
import logging
import time

_CORRELATION_FIELDS = ("worker_id", "job_id", "task_id", "attempt")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CORRELATION_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. re-imported under a test runner)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
