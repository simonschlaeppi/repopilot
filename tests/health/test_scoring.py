"""Unit tests for app.health.scoring.compute_health_score (task 7.13).

Two focused example-based tests:

1. Round-half-up boundary (Req 5.2): a weighted sum that lands exactly on an
   ``x.5`` value where ``x`` is EVEN must round UP to ``x + 1``. This is the
   case where Python's built-in ``round`` (banker's / round-half-to-even) would
   round DOWN to the even ``x`` — so this test guards against banker's rounding
   sneaking back into the composite computation.

2. Import boundary (Req 5.5): the scoring module must operate without importing
   the Analysis_API layer or the Dashboard. We statically parse
   ``app/health/scoring.py`` with the :mod:`ast` module and assert its imports
   are confined to ``math`` and the pure ``app.health`` modules
   (``models``, ``weights``, ``errors``) plus ``__future__`` — and that it does
   NOT reach for I/O / framework / app-entry modules.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import app.health.scoring as scoring_module
from app.health.models import (
    Category,
    ReadmeMetrics,
    RepositorySignals,
)
from app.health.scoring import compute_health_score


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _signals(
    *,
    consistency_ratio: float,
    consistency_available: bool,
    test_files: tuple[str, ...],
    test_coverage_available: bool,
) -> RepositorySignals:
    """Build a RepositorySignals with only the two categories we score exercised.

    Every other category is marked unavailable (score 0). Since the boundary
    test weights those unavailable categories at 0.0, their scores never affect
    the composite — but they remain valid 0-100 scores so no ScoreValueError is
    raised.
    """
    return RepositorySignals(
        readme_text="",
        readme_metrics=ReadmeMetrics(
            length=0,
            heading_count=0,
            code_block_count=0,
            has_install_section=False,
            has_usage_section=False,
        ),
        doc_files=(),
        test_files=test_files,
        ci_files=(),
        lockfiles=(),
        languages={},
        key_files=(),
        dependency_total=0,
        dependency_unpinned=0,
        consistency_ratio=consistency_ratio,
        readme_available=False,
        documentation_available=False,
        test_coverage_available=test_coverage_available,
        dependency_available=False,
        consistency_available=consistency_available,
    )


# --------------------------------------------------------------------------- #
# Test 1 — round-half-up boundary (Req 5.2)
# --------------------------------------------------------------------------- #


def test_composite_rounds_half_up_on_exact_point_five_boundary() -> None:
    """A weighted sum of exactly 50.5 rounds UP to 51 (not down to 50).

    Construction:
    - Weights give Test_Coverage and Readme_Code_Consistency 0.5 each after
      normalization (the other three categories weigh 0.0).
    - Test_Coverage scores 40 (one test file, no CI).
    - Readme_Code_Consistency scores 61 (consistency ratio 0.61 -> 61).
    - Weighted sum = 0.5 * 40 + 0.5 * 61 = 50.5.

    50.5 is a half at an EVEN integer, so:
    - round-half-up (required) -> 51
    - banker's round-half-to-even (Python's round) -> 50

    Asserting the composite is 51 therefore proves half-up rounding, and
    guards against a regression to banker's rounding.
    """
    weights = {
        Category.README_QUALITY: 0.0,
        Category.DOCUMENTATION: 0.0,
        Category.TEST_COVERAGE: 1.0,
        Category.DEPENDENCY_FRESHNESS: 0.0,
        Category.README_CODE_CONSISTENCY: 1.0,
    }

    signals = _signals(
        consistency_ratio=0.61,
        consistency_available=True,
        test_files=("tests/test_example.py",),
        test_coverage_available=True,
    )

    result = compute_health_score(signals, weights)

    # Sanity: confirm the two category scores that drive the boundary.
    scores = {cs.category: cs.score for cs in result.categories}
    assert scores[Category.TEST_COVERAGE] == 40
    assert scores[Category.README_CODE_CONSISTENCY] == 61

    # The weighted sum lands exactly on 50.5 ...
    weighted_sum = 0.5 * 40 + 0.5 * 61
    assert weighted_sum == 50.5

    # ... and must round UP to 51 (half-up), NOT down to 50 (banker's).
    assert result.composite == 51
    assert result.composite != round(weighted_sum)  # round() -> 50 (banker's)
    assert result.composite == math.floor(weighted_sum + 0.5)


# --------------------------------------------------------------------------- #
# Test 2 — import boundary (Req 5.5)
# --------------------------------------------------------------------------- #


def _collect_imported_modules(source: str) -> set[str]:
    """Return the set of top-level module names imported by ``source``.

    Handles both ``import a.b.c`` (records ``a.b.c``) and
    ``from a.b import x`` (records ``a.b``). Relative imports are recorded by
    their module portion if present.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module)
    return modules


def test_scoring_imports_only_allowed_pure_modules() -> None:
    """scoring.py imports only math + app.health pure modules (+ __future__).

    Enforces Req 5.5: the scoring core must not import the Analysis_API layer
    (FastAPI app / app.main) or the Dashboard (Streamlit UI), nor any I/O /
    network / model-provider libraries.
    """
    source = Path(scoring_module.__file__).read_text(encoding="utf-8")
    imported = _collect_imported_modules(source)

    allowed = {
        "__future__",
        "math",
        "app.health.models",
        "app.health.weights",
        "app.health.errors",
    }

    # Every import must be in the allow-list.
    assert imported <= allowed, (
        f"scoring.py imports modules outside the allowed set: "
        f"{sorted(imported - allowed)}"
    )

    # Explicitly assert the forbidden modules are absent (Req 5.5).
    forbidden = {
        "requests",
        "httpx",
        "urllib",
        "urllib.request",
        "fastapi",
        "starlette",
        "streamlit",
        "openai",
        "app.main",
        "app.ui",
        "app.ai",
        "app.github_loader",
        "app.code_analyzer",
    }
    assert imported.isdisjoint(forbidden), (
        f"scoring.py imports forbidden modules: {sorted(imported & forbidden)}"
    )
