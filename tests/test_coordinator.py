from orchestrator.coordinator import propose_refinement
from orchestrator.models import Task, TaskStatus


def _done_task(params: dict, score: float) -> Task:
    task = Task(job_id="job-1", payload={"params": params})
    task.status = TaskStatus.DONE
    task.result = {"params": params, "score": score}
    return task


def test_refinement_zooms_in_around_best_result():
    tasks = [
        _done_task({"fast_ma": 5, "slow_ma": 200}, 96.0),
        _done_task({"fast_ma": 20, "slow_ma": 50}, 10.0),
    ]

    payloads = propose_refinement(tasks, top_n=1)

    assert len(payloads) > 0
    # every proposed point should be numerically close to the best result
    for p in payloads:
        params = p["params"]
        assert abs(params["fast_ma"] - 5) <= 5
        assert abs(params["slow_ma"] - 200) <= 200 * 0.25


def test_refinement_ignores_failed_and_pending_tasks():
    failed = Task(job_id="job-1", payload={"params": {"fast_ma": 1, "slow_ma": 1}})
    failed.status = TaskStatus.FAILED
    pending = Task(job_id="job-1", payload={"params": {"fast_ma": 2, "slow_ma": 2}})

    payloads = propose_refinement([failed, pending], top_n=2)

    assert payloads == []


def test_refinement_deduplicates_overlapping_points():
    tasks = [
        _done_task({"fast_ma": 5, "slow_ma": 200}, 96.0),
        _done_task({"fast_ma": 5, "slow_ma": 200}, 95.9),  # same point, near-tied score
    ]

    payloads = propose_refinement(tasks, top_n=2)
    points = [tuple(sorted(p["params"].items())) for p in payloads]

    assert len(points) == len(set(points))


def test_refinement_with_no_done_tasks_returns_empty():
    assert propose_refinement([], top_n=2) == []
