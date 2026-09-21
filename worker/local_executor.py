"""Direct in-process import fallback for strategy execution. NOT isolated
from the host -- only used when Docker isn't available, and only in
`development` mode (see worker/executor.py). Kept as a fallback so local
demos/tests keep working without Docker Desktop running."""
import importlib.util
from pathlib import Path


def run_local(workdir: Path, params: dict) -> dict:
    strategy_path = workdir / "strategy.py"
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"could not load strategy module from {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    score = module.backtest(params)
    return {"score": score}
