"""Data models for the repo health score feature.

All health data models are **frozen dataclasses** (immutable) to reinforce the
purity and determinism of the scoring core. ``Category`` is a string enum whose
values are the canonical category names used across the API and dashboard.

Note: several dataclasses carry ``dict`` fields (e.g. ``languages``,
``file_contents``). Frozen dataclasses are immutable but not automatically
hashable when they contain unhashable fields such as ``dict``; ``frozen=True``
still guarantees the instances cannot be mutated after construction, which is
what reinforces purity here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    """The five scored health categories.

    Each value is the canonical string name surfaced in API responses and the
    dashboard breakdown.
    """

    README_QUALITY = "README_Quality"
    DOCUMENTATION = "Documentation"
    TEST_COVERAGE = "Test_Coverage"
    DEPENDENCY_FRESHNESS = "Dependency_Freshness"
    README_CODE_CONSISTENCY = "Readme_Code_Consistency"


@dataclass(frozen=True)
class StructuredCodeData:
    """Structured output of the refactored code_analyzer (replaces discarded dict).

    ``file_contents`` is GUARANTEED to include EVERY detected lockfile's content
    (when fetchable) IN ADDITION TO the existing first-5-key-file samples.
    ``files`` retains all detected lockfile paths regardless of the max_files cap.
    """

    files: tuple[str, ...]
    languages: dict[str, int]  # language name -> file count
    key_files: tuple[str, ...]
    file_count: int
    file_contents: dict[str, str]  # path -> content (all lockfiles + key-file samples)


@dataclass(frozen=True)
class ReadmeMetrics:
    """Completeness signals extracted from README text (Req 2.1)."""

    length: int  # char count
    heading_count: int
    code_block_count: int
    has_install_section: bool
    has_usage_section: bool


@dataclass(frozen=True)
class DependencyPinning:
    """Per-lockfile (or aggregated) pinning counts (Req 4)."""

    total: int  # total dependency declarations
    unpinned: int  # count classified as Unpinned_Dependency


@dataclass(frozen=True)
class RepositorySignals:
    """Structured raw inputs to scoring (the Repository_Signals object of Req 1)."""

    readme_text: str
    readme_metrics: ReadmeMetrics
    doc_files: tuple[str, ...]
    test_files: tuple[str, ...]
    ci_files: tuple[str, ...]
    lockfiles: tuple[str, ...]
    languages: dict[str, int]
    key_files: tuple[str, ...]

    dependency_total: int
    dependency_unpinned: int
    consistency_ratio: float  # 0.0-1.0 (Req 3.5)

    # Per-category availability flags (Req 1.6/1.7, 3.6/3.7, 4.6)
    readme_available: bool
    documentation_available: bool
    test_coverage_available: bool
    dependency_available: bool
    consistency_available: bool


@dataclass(frozen=True)
class CategoryScore:
    """A single category's computed score (Req 2, 5.3)."""

    category: Category
    score: int  # 0-100
    missing_data: bool  # true when derived from unavailable signal (Req 2.6)


@dataclass(frozen=True)
class HealthScoreResult:
    """The composite score plus the five category scores (Req 5.3)."""

    composite: int  # 0-100 (Req 5.2)
    categories: tuple[CategoryScore, ...]  # exactly the five categories (Req 5.3)
