import fakeredis
import pytest

from orchestrator.memory import SharedMemory


@pytest.fixture
def memory():
    m = SharedMemory.__new__(SharedMemory)
    m.client = fakeredis.FakeStrictRedis(decode_responses=True)
    return m


def test_retrieve_similar_orders_by_param_distance(memory):
    memory.record({"params": {"fast_ma": 5, "slow_ma": 50}, "score": 10})
    memory.record({"params": {"fast_ma": 5, "slow_ma": 200}, "score": 90})
    memory.record({"params": {"fast_ma": 100, "slow_ma": 110}, "score": 1})

    similar = memory.retrieve_similar({"fast_ma": 5, "slow_ma": 190}, top_k=2)

    assert len(similar) == 2
    assert similar[0]["params"] == {"fast_ma": 5, "slow_ma": 200}


def test_retrieve_similar_returns_empty_when_no_overlap(memory):
    memory.record({"params": {"other_key": 1}, "score": 5})
    assert memory.retrieve_similar({"fast_ma": 5}, top_k=3) == []


def test_retrieve_similar_empty_memory(memory):
    assert memory.retrieve_similar({"fast_ma": 5}, top_k=3) == []
