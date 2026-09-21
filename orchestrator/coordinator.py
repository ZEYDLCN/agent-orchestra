"""Turns a completed job's results into a new, narrower job -- the one
concrete place where one agent's output becomes another's input, instead
of the LLM only commenting on results after the fact.

Deliberately deterministic (not an LLM call): the grid-sweep task shape
here has a real numeric objective (score) to hill-climb toward, so a
reproducible "zoom in around the best results" heuristic is both more
dependable and more testable than asking an LLM to propose new params in
free text and parsing them back out. An LLM-driven planner is the natural
next step once there's a second, non-numeric task type where a heuristic
like this one doesn't apply.
"""
import itertools

from orchestrator.models import Task, TaskStatus


def _grid_around(params: dict, zoom: float) -> list[dict]:
    numeric_keys = [k for k, v in params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numeric_keys:
        return [dict(params)]

    axes = []
    for key in numeric_keys:
        base = params[key]
        delta = max(1.0, abs(base) * zoom)
        low = max(1, round(base - delta))
        high = round(base + delta)
        axes.append(sorted({low, round(base), high}))

    combos = []
    for combo in itertools.product(*axes):
        point = dict(params)
        for key, value in zip(numeric_keys, combo, strict=True):
            point[key] = value
        combos.append(point)
    return combos


def propose_refinement(tasks: list[Task], top_n: int = 2, zoom: float = 0.2) -> list[dict]:
    """Given a completed job's tasks, return new task payloads that zoom
    in around the best-scoring results -- a focused follow-up sweep rather
    than blindly repeating the same grid."""
    scored: list[tuple[Task, dict]] = [
        (t, t.result) for t in tasks if t.status == TaskStatus.DONE and t.result and "score" in t.result
    ]
    scored.sort(key=lambda item: item[1]["score"], reverse=True)

    seen: set[tuple] = set()
    payloads: list[dict] = []
    for _task, result in scored[:top_n]:
        params = result["params"]
        for point in _grid_around(params, zoom):
            key = tuple(sorted(point.items()))
            if key in seen:
                continue
            seen.add(key)
            payloads.append({"params": point})
    return payloads
