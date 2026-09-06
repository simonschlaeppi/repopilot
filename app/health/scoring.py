"""Pure scoring core for the repo health score.

:func:`compute_health_score` is a **pure function** (Req 2.7, 5.4): its output
depends only on the provided :class:`~app.health.models.RepositorySignals` and
:class:`~app.health.models.Category`-keyed weight mapping. It performs no I/O,
uses no randomness, and imports only :mod:`math` and the ``app.health`` pure
modules ``models``, ``weights`` and ``errors`` — enforcing Req 5.5.

Responsibilities (task 7.1):

- Compute the five integer :class:`~app.health.models.CategoryScore` values in
  the range 0-100, with the monotonic directions of Req 2:
    * README_Quality        — non-decreasing as README completeness rises (2.1).
    * Documentation         — presence of doc files scores higher than absence (2.2).
    * Test_Coverage         — presence of test files and CI each raise the score (2.3).
    * Dependency_Freshness  — lockfile presence raises the score; each unpinned
                              dependency reduces it; non-increasing in unpinned
                              for a fixed total (2.4).
    * Readme_Code_Consistency — non-decreasing in ``consistency_ratio`` (2.5).
- Treat an unavailable signal as score 0 with ``missing_data=True``, while still
  including that category in the composite (Req 2.6).
- Normalize the weights at compute time (Req 6.2/6.4) and compute the composite
  as the round-half-up weighted sum, an integer 0-100 (Req 5.1/5.2).
- Raise :class:`~app.health.errors.ScoreValueError` if any computed category
  score falls outside 0-100 (Req 5.6) — a programming-error guard.
"""

from __future__ import annotations

import math

from app.health.errors import ScoreValueError
from app.health.models import (
    Category,
    CategoryScore,
    HealthScoreResult,
    ReadmeMetrics,
    RepositorySignals,
)
from app.health.weights import load_weights, normalize_weights

__all__ = ["compute_health_score"]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def compute_health_score(
    signals: RepositorySignals,
    weights: dict[Category, float] | None = None,
) -> HealthScoreResult:
    """Compute the composite health score and its five category scores.

    Pure function (Req 2.7, 5.4). ``weights`` defaults to the configured
    defaults via :func:`app.health.weights.load_weights` when ``None``; the
    supplied (or default) weights are validated and normalized to sum 1.0 via
    :func:`app.health.weights.normalize_weights` (Req 6.2/6.4), which raises
    :class:`~app.health.errors.WeightConfigError` for an invalid configuration
    (Req 6.5/6.6).

    Each category is scored 0-100 with the monotonic directions of Req 2. An
    unavailable signal yields a score of 0 with ``missing_data=True`` while
    remaining part of the composite (Req 2.6). Any computed category score
    outside 0-100 raises :class:`ScoreValueError` and no composite is returned
    (Req 5.6). The composite is the round-half-up weighted sum, an integer
    0-100 (Req 5.1/5.2).
    """
    active_weights = load_weights() if weights is None else weights
    normalized = normalize_weights(active_weights)

    # Compute each category score and its missing-data flag (Req 2, 2.6).
    category_scores: dict[Category, tuple[int, bool]] = {
        Category.README_QUALITY: _score_readme_quality(signals),
        Category.DOCUMENTATION: _score_documentation(signals),
        Category.TEST_COVERAGE: _score_test_coverage(signals),
        Category.DEPENDENCY_FRESHNESS: _score_dependency_freshness(signals),
        Category.README_CODE_CONSISTENCY: _score_consistency(signals),
    }

    # Guard: every category score must be an integer within 0-100 (Req 5.6).
    for category, (score, _missing) in category_scores.items():
        if not isinstance(score, int) or isinstance(score, bool):
            raise ScoreValueError(category, score)
        if score < 0 or score > 100:
            raise ScoreValueError(category, score)

    # Weighted sum using normalized weights (Req 5.1). Every category is
    # included, even those with missing data (score 0) (Req 2.6).
    weighted_sum = sum(
        normalized[category] * score
        for category, (score, _missing) in category_scores.items()
    )

    # Round half up (Req 5.2). Scores are non-negative so math.floor(x + 0.5)
    # gives round-half-up without banker's rounding.
    composite = math.floor(weighted_sum + 0.5)
    # Clamp defensively to 0-100; with in-range category scores and normalized
    # weights the value is already within range, this guards float edge cases.
    composite = max(0, min(100, composite))

    categories = tuple(
        CategoryScore(
            category=category,
            score=category_scores[category][0],
            missing_data=category_scores[category][1],
        )
        for category in Category
    )

    return HealthScoreResult(composite=composite, categories=categories)


# --------------------------------------------------------------------------- #
# Per-category scoring (Req 2). Each helper returns (score, missing_data).
# An unavailable signal returns (0, True) per Req 2.6.
# --------------------------------------------------------------------------- #


# README completeness dimension weights (out of 100). The length dimension is
# capped so a long README does not dominate; the remaining dimensions reward
# structure and the presence of install/usage sections. Every dimension is
# non-negative, so a README that dominates another in every dimension scores
# at least as high (Req 2.1 monotonicity).
_README_LENGTH_TARGET = 1500  # chars at which the length dimension saturates
_README_LENGTH_POINTS = 30
_README_HEADING_TARGET = 5  # headings at which the heading dimension saturates
_README_HEADING_POINTS = 25
_README_CODE_BLOCK_TARGET = 3  # code blocks at which the dimension saturates
_README_CODE_BLOCK_POINTS = 15
_README_INSTALL_POINTS = 15
_README_USAGE_POINTS = 15


def _score_readme_quality(signals: RepositorySignals) -> tuple[int, bool]:
    """Score README completeness 0-100, non-decreasing in every dimension (Req 2.1).

    Each completeness dimension contributes a non-negative amount that is
    itself monotonic non-decreasing in that dimension (length, heading count,
    code-block count are clamped-linear; install/usage presence are step
    functions). Therefore if one :class:`ReadmeMetrics` dominates another in
    every dimension its score is greater than or equal (Property 2).
    """
    if not signals.readme_available:
        return 0, True

    m: ReadmeMetrics = signals.readme_metrics

    length_score = _clamped_linear(
        m.length, _README_LENGTH_TARGET, _README_LENGTH_POINTS
    )
    heading_score = _clamped_linear(
        m.heading_count, _README_HEADING_TARGET, _README_HEADING_POINTS
    )
    code_block_score = _clamped_linear(
        m.code_block_count, _README_CODE_BLOCK_TARGET, _README_CODE_BLOCK_POINTS
    )
    install_score = _README_INSTALL_POINTS if m.has_install_section else 0.0
    usage_score = _README_USAGE_POINTS if m.has_usage_section else 0.0

    total = (
        length_score
        + heading_score
        + code_block_score
        + install_score
        + usage_score
    )
    return _to_int_score(total), False


def _score_documentation(signals: RepositorySignals) -> tuple[int, bool]:
    """Score documentation presence 0-100 (Req 2.2).

    Presence of any documentation file scores higher than absence. Additional
    documentation files raise the score up to a saturation point, so the score
    is non-decreasing in the count of documentation files (Property 3).
    """
    if not signals.documentation_available:
        return 0, True

    doc_count = len(signals.doc_files)
    if doc_count == 0:
        # documentation_available is derived from doc-file presence, so this
        # branch is defensive; no docs -> 0.
        return 0, False

    # Base credit for any documentation, plus a bounded bonus for more files.
    base = 60
    bonus = _clamped_linear(doc_count - 1, 3, 40)  # up to +40 for 4+ doc files
    return _to_int_score(base + bonus), False


def _score_test_coverage(signals: RepositorySignals) -> tuple[int, bool]:
    """Score test-coverage indicators 0-100 (Req 2.3).

    The presence of test files and of CI configuration each contribute
    non-negative credit, so adding either never decreases the score
    (Property 4). More test files raise the score up to a saturation point.
    """
    if not signals.test_coverage_available:
        return 0, True

    test_count = len(signals.test_files)
    has_ci = bool(signals.ci_files)

    test_score = 0.0
    if test_count > 0:
        # Base credit for having any tests, plus a bounded bonus for more.
        test_score = 40 + _clamped_linear(test_count - 1, 9, 25)  # up to 65
    ci_score = 35.0 if has_ci else 0.0

    return _to_int_score(test_score + ci_score), False


def _score_dependency_freshness(signals: RepositorySignals) -> tuple[int, bool]:
    """Score dependency freshness 0-100 (Req 2.4).

    Lockfile presence yields a base score; each unpinned dependency reduces the
    score proportionally to the unpinned fraction. For a fixed total, the score
    is non-increasing as the unpinned count rises (Property 5), and lockfile
    presence never lowers the score relative to no lockfile.
    """
    if not signals.dependency_available:
        return 0, True

    total = signals.dependency_total
    unpinned = signals.dependency_unpinned

    if total <= 0:
        # Lockfile present but no parseable declarations (rare fallback):
        # presence alone earns a moderate base score.
        return 70, False

    # Clamp unpinned into [0, total] defensively so the ratio stays in [0, 1].
    unpinned = max(0, min(unpinned, total))
    pinned_fraction = (total - unpinned) / total

    # Score scales linearly from a presence base (all unpinned) up to 100
    # (all pinned). Higher unpinned count -> lower pinned_fraction -> lower
    # score, so non-increasing in unpinned for a fixed total (Property 5).
    base = 50.0
    score = base + (100.0 - base) * pinned_fraction
    return _to_int_score(score), False


def _score_consistency(signals: RepositorySignals) -> tuple[int, bool]:
    """Score README-vs-code consistency 0-100 (Req 2.5).

    The score is a monotonic non-decreasing function of ``consistency_ratio``
    (a linear scaling), so a higher ratio yields a score greater than or equal
    to a lower ratio (Property 6).
    """
    if not signals.consistency_available:
        return 0, True

    ratio = signals.consistency_ratio
    # Clamp defensively into [0.0, 1.0]; the extractor already guarantees range.
    ratio = max(0.0, min(1.0, ratio))
    return _to_int_score(ratio * 100.0), False


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #


def _clamped_linear(value: int, target: int, max_points: float) -> float:
    """Return ``max_points`` scaled by ``min(value, target) / target``.

    Non-decreasing in ``value`` and clamped to ``[0, max_points]``. Guards a
    non-positive ``target`` by returning ``max_points`` for any positive value.
    """
    if value <= 0:
        return 0.0
    if target <= 0:
        return max_points
    return max_points * min(value, target) / target


def _to_int_score(value: float) -> int:
    """Convert a raw float score to an integer 0-100, rounding half up.

    Clamps into ``[0, 100]`` defensively; the per-category maxima already sum
    to 100 so in-range inputs stay in range.
    """
    rounded = math.floor(value + 0.5)
    return max(0, min(100, rounded))
