"""Unit tests for app.health.models.

Covers:
- The ``Category`` enum has exactly the five defined members with the expected
  canonical string values (Requirements 1.2, 5.3).
- Every health dataclass is frozen: mutating an attribute raises
  ``FrozenInstanceError`` (reinforcing the purity of the scoring core).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.health.models import (
    Category,
    CategoryScore,
    DependencyPinning,
    HealthScoreResult,
    ReadmeMetrics,
    RepositorySignals,
    StructuredCodeData,
)


# --------------------------------------------------------------------------- #
# Category enum
# --------------------------------------------------------------------------- #

EXPECTED_CATEGORY_VALUES = {
    "README_QUALITY": "README_Quality",
    "DOCUMENTATION": "Documentation",
    "TEST_COVERAGE": "Test_Coverage",
    "DEPENDENCY_FRESHNESS": "Dependency_Freshness",
    "README_CODE_CONSISTENCY": "Readme_Code_Consistency",
}


def test_category_has_exactly_five_members():
    """Category defines exactly the five scored health categories (Req 5.3)."""
    assert len(Category) == 5
    assert {member.name for member in Category} == set(EXPECTED_CATEGORY_VALUES)


@pytest.mark.parametrize(
    ("name", "value"), sorted(EXPECTED_CATEGORY_VALUES.items())
)
def test_category_member_values(name, value):
    """Each member carries its expected canonical string value (Req 1.2, 5.3)."""
    member = Category[name]
    assert member.value == value


def test_category_is_str_enum():
    """Category values are usable as their canonical strings (str Enum)."""
    assert Category.README_QUALITY == "README_Quality"
    assert isinstance(Category.DOCUMENTATION, str)


# --------------------------------------------------------------------------- #
# Frozen dataclass immutability
# --------------------------------------------------------------------------- #


def _structured_code_data() -> StructuredCodeData:
    return StructuredCodeData(
        files=("a.py",),
        languages={"Python": 1},
        key_files=("a.py",),
        file_count=1,
        file_contents={"a.py": "print('hi')"},
    )


def _readme_metrics() -> ReadmeMetrics:
    return ReadmeMetrics(
        length=100,
        heading_count=3,
        code_block_count=1,
        has_install_section=True,
        has_usage_section=False,
    )


def _dependency_pinning() -> DependencyPinning:
    return DependencyPinning(total=5, unpinned=2)


def _repository_signals() -> RepositorySignals:
    return RepositorySignals(
        readme_text="hello",
        readme_metrics=_readme_metrics(),
        doc_files=("docs/x.md",),
        test_files=("tests/test_x.py",),
        ci_files=(".github/workflows/ci.yml",),
        lockfiles=("requirements.txt",),
        languages={"Python": 1},
        key_files=("a.py",),
        dependency_total=5,
        dependency_unpinned=2,
        consistency_ratio=0.5,
        readme_available=True,
        documentation_available=True,
        test_coverage_available=True,
        dependency_available=True,
        consistency_available=True,
    )


def _category_score() -> CategoryScore:
    return CategoryScore(
        category=Category.README_QUALITY, score=80, missing_data=False
    )


def _health_score_result() -> HealthScoreResult:
    return HealthScoreResult(composite=75, categories=(_category_score(),))


@pytest.mark.parametrize(
    ("instance", "attr", "new_value"),
    [
        (_structured_code_data(), "file_count", 99),
        (_readme_metrics(), "length", 0),
        (_dependency_pinning(), "total", 0),
        (_repository_signals(), "readme_text", "changed"),
        (_category_score(), "score", 0),
        (_health_score_result(), "composite", 0),
    ],
)
def test_dataclasses_are_frozen(instance, attr, new_value):
    """Mutating any health dataclass attribute raises FrozenInstanceError."""
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attr, new_value)


def test_frozen_blocks_new_attribute():
    """Frozen dataclasses reject setting entirely new attributes too."""
    result = _health_score_result()
    with pytest.raises(FrozenInstanceError):
        result.some_new_field = 1  # type: ignore[attr-defined]
