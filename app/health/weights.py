"""Configuration-driven category weights for the repo health score.

Weights live here, in a module separate from the scoring computation, so that
tuning them requires no change to the scoring logic (Req 6.1-6.3). The scoring
core reads and normalizes these weights at compute time.

``DEFAULT_WEIGHTS`` defines exactly one non-negative numeric weight for each of
the five :class:`~app.health.models.Category` members. ``load_weights`` returns
the active weights (defaults, optionally overridden), and ``normalize_weights``
validates and rescales any weight mapping so the weights sum to 1.0, raising
:class:`~app.health.errors.WeightConfigError` for invalid configurations.
"""

from __future__ import annotations

from numbers import Real

from app.health.errors import WeightConfigError
from app.health.models import Category

# Exactly one non-negative weight per category (Req 6.1). These are raw
# (non-normalized) weights; ``normalize_weights`` rescales them to sum 1.0.
DEFAULT_WEIGHTS: dict[Category, float] = {
    Category.README_QUALITY: 0.25,
    Category.DOCUMENTATION: 0.20,
    Category.TEST_COVERAGE: 0.25,
    Category.DEPENDENCY_FRESHNESS: 0.15,
    Category.README_CODE_CONSISTENCY: 0.15,
}


def load_weights(
    overrides: dict[Category, float] | None = None,
) -> dict[Category, float]:
    """Return the active weights (defaults unless overridden).

    Starts from a copy of :data:`DEFAULT_WEIGHTS` and applies any ``overrides``
    on top. The returned mapping is a fresh ``dict`` so callers cannot mutate
    the module-level defaults. Validation and normalization are performed by
    :func:`normalize_weights`.
    """
    weights: dict[Category, float] = dict(DEFAULT_WEIGHTS)
    if overrides:
        weights.update(overrides)
    return weights


def normalize_weights(
    weights: dict[Category, float],
) -> dict[Category, float]:
    """Validate then normalize ``weights`` so the values sum to 1.0.

    Raises :class:`WeightConfigError` for:

    - a missing category (identifies it by name, Req 6.5),
    - a negative or non-numeric weight (Req 6.6),
    - a set of weights that sums to zero (Req 6.6).

    Booleans are rejected as non-numeric even though ``bool`` is a subclass of
    ``int`` in Python.
    """
    # Missing category (Req 6.5) - name the first missing category.
    for category in Category:
        if category not in weights:
            raise WeightConfigError(
                f"Weight configuration is missing a weight for category "
                f"{category.value!r}"
            )

    # Negative or non-numeric weight (Req 6.6).
    for category, weight in weights.items():
        # bool is a subclass of int/Real; reject it as non-numeric.
        if isinstance(weight, bool) or not isinstance(weight, Real):
            raise WeightConfigError(
                f"Weight for category {category.value!r} is not a number: "
                f"{weight!r}"
            )
        if weight < 0:
            raise WeightConfigError(
                f"Weight for category {category.value!r} is negative: {weight!r}"
            )

    # Sum of zero (Req 6.6).
    total = sum(float(weights[category]) for category in Category)
    if total == 0:
        raise WeightConfigError("Weight configuration sums to zero")

    # Normalize to sum 1.0 (Req 6.4).
    return {category: float(weights[category]) / total for category in Category}
