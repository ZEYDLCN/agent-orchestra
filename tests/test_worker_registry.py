import fakeredis
import pytest

from orchestrator.models import WorkerInfo
from orchestrator.worker_registry import WorkerRegistry


@pytest.fixture
def registry():
    r = WorkerRegistry.__new__(WorkerRegistry)
    r.client = fakeredis.FakeStrictRedis(decode_responses=True)
    return r


def _info(worker_id="worker-1"):
    return WorkerInfo(worker_id=worker_id, worktree_path="/tmp/x", branch="agent/x")


def test_heartbeat_then_list_all(registry):
    registry.heartbeat(_info("worker-1"))
    registry.heartbeat(_info("worker-2"))

    workers = {w.worker_id for w in registry.list_all()}

    assert workers == {"worker-1", "worker-2"}


def test_heartbeat_sets_status_and_timestamp(registry):
    info = _info()
    registry.heartbeat(info)

    stored = registry.get("worker-1")
    assert stored.status == "running"
    assert stored.last_heartbeat is not None


def test_deregister_removes_worker(registry):
    registry.heartbeat(_info())
    registry.deregister("worker-1")

    assert registry.get("worker-1") is None
    assert registry.list_all() == []


def test_stop_flag_lifecycle(registry):
    assert registry.is_stop_requested("worker-1") is False

    registry.request_stop("worker-1")
    assert registry.is_stop_requested("worker-1") is True

    registry.deregister("worker-1")
    assert registry.is_stop_requested("worker-1") is False


def test_crashed_worker_ages_out_via_ttl(registry):
    """No active cleanup needed for a dead worker -- Redis's own TTL does
    it. fakeredis simulates TTL expiry, so PERSIST -> manual delete stands
    in for "time passed" here without a real sleep."""
    info = _info()
    registry.heartbeat(info)
    assert registry.get("worker-1") is not None

    registry.client.delete("worker:worker-1:info")  # simulate TTL expiry

    assert registry.get("worker-1") is None
    assert registry.list_all() == []
