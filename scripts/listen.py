"""Live feed of inter-agent messages for a job. Run alongside demo.py to
show worker-to-worker / worker-to-orchestrator broadcast events as they
happen.

Usage:
    python scripts/listen.py <job_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.config import settings  # noqa: E402
from orchestrator.messaging import MessageBus  # noqa: E402


def main(job_id: str):
    bus = MessageBus(settings.redis_url)
    print(f"-> listening on job {job_id} (ctrl+c to stop)")
    for event in bus.subscribe(job_id):
        data = event["data"]
        print(
            f"[{event['type']}] worker={data.get('worker_id')} "
            f"params={data.get('params')} score={data.get('score')} "
            f"commentary={data.get('commentary')!r}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/listen.py <job_id>")
        sys.exit(1)
    try:
        main(sys.argv[1])
    except KeyboardInterrupt:
        pass
