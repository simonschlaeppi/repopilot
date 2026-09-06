"""Error types for the repo health score feature.

These exceptions form a small hierarchy rooted at :class:`HealthScoreError`
so callers can catch all health-scoring errors with a single ``except`` while
still distinguishing specific failure modes when needed.
"""

from __future__ import annotations


class HealthScoreError(Exception):
    """Base class for all repo health score errors."""


class ScoreValueError(HealthScoreError):
    """Raised when a computed category score falls outside 0-100 (Req 5.6).

    The offending category is captured so the caller can identify which
    category produced the out-of-range score.
    """

    def __init__(self, category, score) -> None:
        self.category = category
        self.score = score
        category_name = getattr(category, "value", category)
        super().__init__(
            f"Category score for {category_name!r} is out of range 0-100: {score!r}"
        )


class WeightConfigError(HealthScoreError):
    """Raised for an invalid weight configuration (Req 6.5, 6.6).

    Covers a missing category weight (named), a negative or non-numeric
    weight, or a set of weights that sums to zero.
    """
