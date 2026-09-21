import threading
import time

import fakeredis
import pytest

from orchestrator.messaging import MessageBus


@pytest.fixture
def shared_server():
    return fakeredis.FakeServer()


def _bus(server) -> MessageBus:
    b = MessageBus.__new__(MessageBus)
    b.client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    return b


def test_publish_subscribe_roundtrip(shared_server):
    subscriber = _bus(shared_server)
    publisher = _bus(shared_server)
    received: list[dict] = []

    def listen():
        for event in subscriber.subscribe("job-1"):
            received.append(event)
            break

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(0.2)  # let the SUBSCRIBE register before we publish

    publisher.publish("job-1", "task_result", {"worker_id": "w1", "score": 42})
    t.join(timeout=2)

    assert len(received) == 1
    assert received[0]["type"] == "task_result"
    assert received[0]["data"] == {"worker_id": "w1", "score": 42}


def test_late_subscriber_replays_events_published_before_it_connected(shared_server):
    publisher = _bus(shared_server)
    publisher.publish("job-2", "task_result", {"worker_id": "w1", "score": 10})
    publisher.publish("job-2", "task_result", {"worker_id": "w2", "score": 20})

    subscriber = _bus(shared_server)
    received = []
    for event in subscriber.subscribe("job-2"):
        received.append(event)
        if len(received) == 2:
            break

    assert [e["data"]["worker_id"] for e in received] == ["w1", "w2"]
