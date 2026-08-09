"""Leakage-safe backtesting and weight optimisation."""

from .config import OptimizerConfig
from .data import attach_forward_returns, load_snapshot_files
from .engine import BacktestOptimizer, OptimizationResult

__all__ = ["BacktestOptimizer", "OptimizationResult", "OptimizerConfig", "attach_forward_returns", "load_snapshot_files"]
