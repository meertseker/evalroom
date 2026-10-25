"""Entry point Inspect loads (``[project.entry-points.inspect_ai]``): registers the metrics."""

from .metric import wilson_accuracy

__all__ = ["wilson_accuracy"]
