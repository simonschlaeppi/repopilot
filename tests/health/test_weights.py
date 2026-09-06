"""Unit tests for app.health.weights.

Covers the weight-configuration defaults and normalization edges for task 3.5:

- ``DEFAULT_WEIGHTS`` defines exactly one weight for each of the five
  ``Category`` members and every weight is non-negative (Requirement 6.1).
- ``load_weights`` returns the defaults and applies caller overrides on top
  without mutating the module-level defaults (Requirement 6.1).
- ``normalize_weights`` rescales explicit non-normalized inputs so the values
  sum to 1.0 within float tolerance (Requirement 6.4).
"""

from __future__ import annotations

import math

import pytest

from app.health.models import Category
from app.health.weights import DEFAULT_WEIGHTS, load_weights, normalize_weights


# --------------------------------------------------------------------------- #
# DEFAULT_WEIGHTS defaults (Requirement 6.1)
# --------------------------------------------------------------------------- #


def test_default_weights_defines_all_five_categories():
    """DEFAULT_WEIGHTS has exactly one weight for each of the five categories."""
    assert set(DEFAULT_WEIGHTS) == set(Category)
    assert len(DEFAULT_WEIGHTS) == 5


@pytest.mark.parametrize("category", list(Category))
def test_default_weights_are_non_negative_numbers(category):
    """Every default weight is a non-negative number (Req 6.1)."""
    weight = DEFAULT_WEIGHTS[category]
    assert isinstance(weight, (int, float))
    assert not isinstance(weight, bool)
    assert weight >= 0


# --------------------------------------------------------------------------- #
# load_weights (Requirement 6.1)
# --------------------------------------------------------------------------- #


def test_load_weights_returns_defaults():
    """load_weights() with no overrides returns the defaults."""
    assert load_weights() == DEFAULT_WEIGHTS


def test_load_weights_returns_fresh_copy():
    """The returned mapping is a fresh dict, not the module-level defaults."""
    loaded = load_weights()
    loaded[Category.README_QUALITY] = 999.0
    assert DEFAULT_WEIGHTS[Category.README_QUALITY] != 999.0


def test_load_weights_applies_overrides():
    """load_weights applies overrides on top of the defaults (Req 6.1)."""
    overrides = {Category.TEST_COVERAGE: 0.5}
    loaded = load_weights(overrides)

    assert loaded[Category.TEST_COVERAGE] == 0.5
    # Non-overridden categories keep their default values.
    assert loaded[Category.README_QUALITY] == DEFAULT_WEIGHTS[Category.README_QUALITY]
    # All five categories are still present.
    assert set(loaded) == set(Category)


# --------------------------------------------------------------------------- #
# normalize_weights normalization edges (Requirement 6.4)
# --------------------------------------------------------------------------- #


def test_normalize_weights_defaults_sum_to_one():
    """Normalizing the defaults produces weights summing to 1.0."""
    normalized = normalize_weights(dict(DEFAULT_WEIGHTS))
    assert math.isclose(sum(normalized.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9)


def test_normalize_weights_non_normalized_input_sums_to_one():
    """Explicit non-normalized weights normalize to sum 1.0 (Req 6.4)."""
    weights = {
        Category.README_QUALITY: 2.0,
        Category.DOCUMENTATION: 2.0,
        Category.TEST_COVERAGE: 2.0,
        Category.DEPENDENCY_FRESHNESS: 2.0,
        Category.README_CODE_CONSISTENCY: 2.0,
    }  # sums to 10.0, not 1.0

    normalized = normalize_weights(weights)

    assert math.isclose(sum(normalized.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9)
    # Equal raw weights normalize to equal shares.
    for value in normalized.values():
        assert math.isclose(value, 0.2, rel_tol=1e-9, abs_tol=1e-9)


def test_normalize_weights_preserves_relative_proportions():
    """Normalization preserves the relative proportions of the raw weights."""
    weights = {
        Category.README_QUALITY: 4.0,
        Category.DOCUMENTATION: 1.0,
        Category.TEST_COVERAGE: 1.0,
        Category.DEPENDENCY_FRESHNESS: 1.0,
        Category.README_CODE_CONSISTENCY: 1.0,
    }  # sums to 8.0

    normalized = normalize_weights(weights)

    assert math.isclose(sum(normalized.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(
        normalized[Category.README_QUALITY], 0.5, rel_tol=1e-9, abs_tol=1e-9
    )
    assert math.isclose(
        normalized[Category.DOCUMENTATION], 0.125, rel_tol=1e-9, abs_tol=1e-9
    )


def test_normalize_weights_with_a_zero_weight_still_sums_to_one():
    """A single zero weight is allowed and the rest still normalize to 1.0."""
    weights = {
        Category.README_QUALITY: 0.0,
        Category.DOCUMENTATION: 1.0,
        Category.TEST_COVERAGE: 1.0,
        Category.DEPENDENCY_FRESHNESS: 1.0,
        Category.README_CODE_CONSISTENCY: 1.0,
    }

    normalized = normalize_weights(weights)

    assert math.isclose(sum(normalized.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9)
    assert normalized[Category.README_QUALITY] == 0.0
