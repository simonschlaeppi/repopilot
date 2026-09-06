"""Repo health score package.

Pure scoring core for RepoPilot's repository health score. This package has
no dependency on the network, API, or UI layers (``requests``, ``fastapi``,
``streamlit``, ``openai``, or ``app.main``).

Exports the error types, the data models, the configuration-driven weights,
the lockfile pinning parser, signal extraction, and the pure scoring core.
"""

from __future__ import annotations

from app.health.errors import (
    HealthScoreError,
    ScoreValueError,
    WeightConfigError,
)
from app.health.lockfile import parse_pinning
from app.health.models import (
    Category,
    CategoryScore,
    DependencyPinning,
    HealthScoreResult,
    ReadmeMetrics,
    RepositorySignals,
    StructuredCodeData,
)
from app.health.scoring import compute_health_score
from app.health.signals import extract_signals
from app.health.weights import (
    DEFAULT_WEIGHTS,
    load_weights,
    normalize_weights,
)

__all__ = [
    "HealthScoreError",
    "ScoreValueError",
    "WeightConfigError",
    "Category",
    "CategoryScore",
    "DependencyPinning",
    "HealthScoreResult",
    "ReadmeMetrics",
    "RepositorySignals",
    "StructuredCodeData",
    "DEFAULT_WEIGHTS",
    "load_weights",
    "normalize_weights",
    "parse_pinning",
    "extract_signals",
    "compute_health_score",
]
