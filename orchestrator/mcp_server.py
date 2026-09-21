"""MCP server exposing the orchestrator as tools for any MCP client
(Claude Desktop, Claude Code, another agent) to drive directly -- submit
jobs, scale the worker cluster, and read back results.

Thin HTTP client over the FastAPI orchestrator (not a second in-process
WorkerManager), so it can run standalone alongside `uvicorn orchestrator.main:app`.

Run:
    python -m orchestrator.mcp_server
"""
import requests
from mcp.server.mcpserver import MCPServer

from orchestrator.config import settings

mcp = MCPServer("agent-orchestrator")


@mcp.tool()
def submit_job(tasks: list[dict]) -> dict:
    """Submit a job: a list of task payloads (e.g. [{"params": {"fast_ma": 5, "slow_ma": 50}}])
    to be fanned out across the worker cluster. Returns job_id and task_ids."""
    resp = requests.post(f"{settings.orchestrator_base_url}/jobs", json={"tasks": tasks})
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_job_status(job_id: str) -> dict:
    """Get the status and results of a previously submitted job."""
    resp = requests.get(f"{settings.orchestrator_base_url}/jobs/{job_id}")
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def scale_workers(count: int) -> list[dict]:
    """Scale the worker cluster up to `count` workers, each in its own
    isolated git worktree."""
    resp = requests.post(f"{settings.orchestrator_base_url}/workers/scale", json={"count": count})
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_workers() -> list[dict]:
    """List currently running workers and their worktree paths."""
    resp = requests.get(f"{settings.orchestrator_base_url}/workers")
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    mcp.run()
