"""Optional shared-secret auth. Unset ORCH_API_KEY (the default) keeps the
local/demo experience frictionless -- this is a local dev tool by default,
not a public service. Setting it gates mutating endpoints behind a
`X-API-Key` header, for anyone who exposes this beyond localhost.

Not wired into the bundled dashboard's own fetch calls (no login UI here);
when ORCH_API_KEY is set, the dashboard needs the header added separately
or a reverse proxy in front of it. Documented, not silently half-built.
"""
from fastapi import Header, HTTPException

from orchestrator.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key is None:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(401, "missing or invalid X-API-Key")
