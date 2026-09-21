"""Runs inside the sandbox container only. Imports strategy.py from the
read-only /workspace mount (the task's git worktree) and evaluates it with
the given params. Never trust this process with anything the host cares
about -- it gets no network, no host filesystem beyond the read-only
mount, no environment variables from the host, and a non-root user."""
import importlib.util
import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: runner.py <params_json>"}), file=sys.stderr)
        return 1

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid params JSON: {exc}"}), file=sys.stderr)
        return 1

    try:
        spec = importlib.util.spec_from_file_location("strategy", "/workspace/strategy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        score = module.backtest(params)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"score": score}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
