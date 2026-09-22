import fakeredis
import pytest

from orchestrator.queue import TaskQueue
from orchestrator.reaper import Reaper


@pytest.fixture
def shared_server():
    return fakeredis.FakeServer()


def _reaper(server) -> Reaper:
    queue = TaskQueue()
    queue.rebind_client(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    return Reaper(queue)


def test_first_reaper_becomes_leader(shared_server):
    reaper = _reaper(shared_server)
    assert reaper._try_become_or_stay_leader() is True
    assert reaper._is_leader is True


def test_second_reaper_does_not_become_leader_while_first_holds_it(shared_server):
    first = _reaper(shared_server)
    second = _reaper(shared_server)

    assert first._try_become_or_stay_leader() is True
    assert second._try_become_or_stay_leader() is False
    assert second._is_leader is False


def test_leader_keeps_leadership_across_ticks(shared_server):
    reaper = _reaper(shared_server)
    assert reaper._try_become_or_stay_leader() is True

    # simulate several more ticks -- should keep renewing, not re-acquire
    for _ in range(3):
        assert reaper._try_become_or_stay_leader() is True


def test_stop_releases_leadership_for_another_instance(shared_server):
    first = _reaper(shared_server)
    second = _reaper(shared_server)

    assert first._try_become_or_stay_leader() is True
    assert second._try_become_or_stay_leader() is False

    first.stop()  # no thread was started, but this should still release the lock

    assert second._try_become_or_stay_leader() is True


def test_second_reaper_takes_over_if_first_never_renews(shared_server):
    """Simulates the first leader's process dying: its lock just expires
    (fakeredis honors TTL), no explicit release happens."""
    first = _reaper(shared_server)
    assert first._try_become_or_stay_leader() is True

    from orchestrator.reaper import LEADER_KEY

    first._queue.client.delete(LEADER_KEY)  # stand-in for TTL expiry

    second = _reaper(shared_server)
    assert second._try_become_or_stay_leader() is True
