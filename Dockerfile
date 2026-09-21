# Orchestrator + worker image. Workers are spawned as sibling subprocesses
# inside this same container (git worktrees under /app/workspace), so git
# must be present here. Task *execution* itself still runs sandboxed in its
# own separate container (see sandbox/Dockerfile) via the host's Docker
# daemon -- this container needs the Docker socket mounted to launch those
# (see docker-compose.yml). Mounting the host socket grants this container
# real control over the host's Docker daemon; that's an accepted tradeoff
# for a small/self-hosted deployment, not something to do on a shared or
# multi-tenant host without further isolation (rootless Docker, a proxy
# that scopes what the socket allows, or a dedicated Docker-in-Docker VM).
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl docker.io \
    && rm -rf /var/lib/apt/lists/*

# match the sandbox container's uid so bind-mounted worktrees are readable
RUN useradd -m -u 1000 orchestrator

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R orchestrator:orchestrator /app

USER orchestrator

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
