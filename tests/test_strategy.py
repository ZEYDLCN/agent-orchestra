import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "target_repo_seed"))

from strategy import backtest  # noqa: E402


def test_backtest_is_deterministic():
    params = {"fast_ma": 10, "slow_ma": 50}
    assert backtest(params) == backtest(params)


def test_backtest_prefers_wider_spread():
    narrow = backtest({"fast_ma": 40, "slow_ma": 50})
    wide = backtest({"fast_ma": 5, "slow_ma": 200})
    assert wide > narrow
