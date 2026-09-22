"""Redis-backed LLM call tracing shared by control-plane and worker agents."""

import logging
import time
from collections import defaultdict
from typing import Any, cast

from orchestrator.config import settings
from orchestrator.llm.base import LLMProvider, LLMResponse, TokenUsage, estimate_tokens
from orchestrator.models import LLMTrace
from orchestrator.redis_client import make_redis_client

logger = logging.getLogger(__name__)

TRACE_INDEX_KEY = "llm_traces:index"


def _trace_key(trace_id: str) -> str:
    return f"llm_trace:{trace_id}"


def _scope_key(scope: str, value: str) -> str:
    return f"llm_traces:{scope}:{value}"


def _provider_parts(name: str) -> tuple[str, str | None]:
    provider, separator, model = name.partition(":")
    return provider, model if separator else None


def _pricing() -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for entry in settings.llm_pricing_raw.split(";"):
        if not entry.strip():
            continue
        try:
            name, input_price, output_price = (part.strip() for part in entry.split("|"))
            result[name] = (float(input_price), float(output_price))
        except (TypeError, ValueError):
            logger.warning("ignoring invalid ORCH_LLM_PRICING_RAW entry: %s", entry)
    return result


def estimate_cost(provider_name: str, usage: TokenUsage) -> float | None:
    provider, _ = _provider_parts(provider_name)
    if provider in {"mock", "ollama"}:
        return 0.0
    prices = _pricing().get(provider_name)
    if prices is None:
        return None
    input_price, output_price = prices
    return round(
        (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000,
        8,
    )


class TraceStore:
    def __init__(self, redis_url: str | None = None):
        self.client = make_redis_client(redis_url)

    def rebind_client(self, client) -> None:
        self.client = client

    def record(self, trace: LLMTrace) -> None:
        score = trace.started_at
        index_keys = [TRACE_INDEX_KEY]
        for scope, value in (
            ("run", trace.run_id),
            ("job", trace.job_id),
            ("task", trace.task_id),
            ("agent", trace.agent_id),
        ):
            if value:
                index_keys.append(_scope_key(scope, value))

        pipe = self.client.pipeline()
        pipe.set(_trace_key(trace.trace_id), trace.model_dump_json(), ex=settings.task_ttl_seconds)
        for index_key in index_keys:
            pipe.zadd(index_key, {trace.trace_id: score})
            pipe.zremrangebyrank(index_key, 0, -settings.job_index_max_entries - 1)
            pipe.expire(index_key, settings.task_ttl_seconds)
        pipe.execute()

    def get(self, trace_id: str) -> LLMTrace | None:
        raw = cast(str | None, self.client.get(_trace_key(trace_id)))
        return LLMTrace.model_validate_json(raw) if raw else None

    def list_recent(
        self,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
        limit: int = 100,
    ) -> list[LLMTrace]:
        index_key = (
            _scope_key("run", run_id)
            if run_id
            else _scope_key("job", job_id)
            if job_id
            else _scope_key("agent", agent_id)
            if agent_id
            else TRACE_INDEX_KEY
        )
        # Read a wider window when a secondary filter is present, while
        # keeping Redis work bounded.
        scan_limit = min(settings.job_index_max_entries, limit * 5)
        trace_ids = cast(list[str], self.client.zrevrange(index_key, 0, scan_limit - 1))
        traces: list[LLMTrace] = []
        for trace_id in trace_ids:
            trace = self.get(trace_id)
            if trace is None:
                continue
            if run_id and trace.run_id != run_id:
                continue
            if job_id and trace.job_id != job_id:
                continue
            if agent_id and trace.agent_id != agent_id:
                continue
            if role and trace.role != role:
                continue
            traces.append(trace)
            if len(traces) >= limit:
                break
        return traces

    def summary(self, **filters: Any) -> dict[str, Any]:
        traces = self.list_recent(limit=settings.job_index_max_entries, **filters)
        agents: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "duration_ms": 0.0,
                "cost_usd": 0.0,
                "cost_available": False,
                "failed_calls": 0,
                "estimated_calls": 0,
                "roles": set(),
                "providers": set(),
            }
        )
        total_cost = 0.0
        cost_available = False
        for trace in traces:
            item = agents[trace.agent_id]
            item["calls"] += 1
            item["input_tokens"] += trace.input_tokens
            item["output_tokens"] += trace.output_tokens
            item["total_tokens"] += trace.total_tokens
            item["duration_ms"] += trace.duration_ms
            item["failed_calls"] += int(trace.status != "completed")
            item["estimated_calls"] += int(trace.tokens_estimated)
            item["roles"].add(trace.role)
            item["providers"].add(trace.provider + (f":{trace.model}" if trace.model else ""))
            if trace.cost_usd is not None:
                item["cost_usd"] += trace.cost_usd
                item["cost_available"] = True
                total_cost += trace.cost_usd
                cost_available = True

        agent_rows = []
        for agent_id, item in agents.items():
            item["agent_id"] = agent_id
            item["duration_ms"] = round(item["duration_ms"], 2)
            item["cost_usd"] = round(item["cost_usd"], 8) if item.pop("cost_available") else None
            item["roles"] = sorted(item["roles"])
            item["providers"] = sorted(item["providers"])
            agent_rows.append(item)
        agent_rows.sort(key=lambda item: item["total_tokens"], reverse=True)
        return {
            "calls": len(traces),
            "input_tokens": sum(trace.input_tokens for trace in traces),
            "output_tokens": sum(trace.output_tokens for trace in traces),
            "total_tokens": sum(trace.total_tokens for trace in traces),
            "duration_ms": round(sum(trace.duration_ms for trace in traces), 2),
            "cost_usd": round(total_cost, 8) if cost_available else None,
            "failed_calls": sum(trace.status != "completed" for trace in traces),
            "agents": agent_rows,
        }


def traced_generate(
    llm: LLMProvider,
    prompt: str,
    trace_store: TraceStore | None,
    *,
    role: str,
    agent_id: str,
    run_id: str | None = None,
    job_id: str | None = None,
    task_id: str | None = None,
    round_number: int | None = None,
) -> LLMResponse:
    started_at = time.time()
    started = time.perf_counter()
    provider, model = _provider_parts(llm.name)
    try:
        if hasattr(llm, "generate_with_usage"):
            response = llm.generate_with_usage(prompt)
        else:
            # Lightweight test/custom providers written against the original
            # generate-only interface remain compatible and are clearly
            # labelled as estimates.
            text = llm.generate(prompt)
            response = LLMResponse(
                text=text,
                usage=TokenUsage(
                    input_tokens=estimate_tokens(prompt),
                    output_tokens=estimate_tokens(text),
                    estimated=True,
                ),
            )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        if trace_store is not None:
            _safe_record(
                trace_store,
                LLMTrace(
                    run_id=run_id,
                    job_id=job_id,
                    task_id=task_id,
                    round_number=round_number,
                    role=role,
                    agent_id=agent_id,
                    provider=provider,
                    model=model,
                    status="timeout" if "timeout" in type(exc).__name__.lower() else "failed",
                    input_tokens=estimate_tokens(prompt),
                    total_tokens=estimate_tokens(prompt),
                    tokens_estimated=True,
                    duration_ms=round(duration_ms, 2),
                    error=str(exc)[:1000],
                    started_at=started_at,
                    completed_at=time.time(),
                ),
            )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    if trace_store is not None:
        _safe_record(
            trace_store,
            LLMTrace(
                run_id=run_id,
                job_id=job_id,
                task_id=task_id,
                round_number=round_number,
                role=role,
                agent_id=agent_id,
                provider=provider,
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
                tokens_estimated=response.usage.estimated,
                duration_ms=round(duration_ms, 2),
                cost_usd=estimate_cost(llm.name, response.usage),
                started_at=started_at,
                completed_at=time.time(),
            ),
        )
    return response


def _safe_record(store: TraceStore, trace: LLMTrace) -> None:
    try:
        store.record(trace)
    except Exception:  # noqa: BLE001
        logger.exception("could not persist LLM trace %s", trace.trace_id)
