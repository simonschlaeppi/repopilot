"""API integration tests for the health-scoring wiring in ``app/main.py`` (task 10.2).

These exercise the ``/analyze`` endpoint end-to-end through FastAPI's
``TestClient`` while mocking the external fetchers so no network access occurs:

- ``app.main.get_readme`` -> a canned README string.
- ``app.main.analyze_repository`` -> a canned :class:`StructuredCodeData`.
- ``app.main.explain_repo`` / ``explain_repo_with_code`` -> a canned analysis string.

``HEALTH_SCORING_ENABLED`` is read at import time (module level), so it is
toggled per-test by ``monkeypatch.setattr(app.main, "HEALTH_SCORING_ENABLED", ...)``
which auto-restores the original value after each test.

Covered acceptance criteria:

- Enabled: ``health`` block with a composite plus five in-range category scores (7.1, 7.2).
- Additive: ``repo`` / ``analysis`` / ``includes_code`` unchanged alongside ``health`` (7.3).
- Reuse: only the existing analysis fetches happen — no extra calls (7.4).
- Missing data: a forced-unavailable signal surfaces ``missing_data: true`` (7.5).
- Failure path: a forced extraction/scoring exception yields the existing fields
  plus ``health.available == false`` with a non-empty reason (7.6).
- Disabled (backward compat): no ``health`` key, original response shape.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.health.models import StructuredCodeData


CANNED_README = """# Example Project

A small Python project.

## Installation

```bash
pip install example
```

## Usage

Use `requirements.txt` to install dependencies. Written in Python.
"""

CANNED_ANALYSIS = "This repository is a small Python web service."


def _rich_structured_data() -> StructuredCodeData:
    """StructuredCodeData with every signal available.

    Includes a lockfile with content (dependency scoring available), languages
    and key_files (consistency available on the code side), documentation and
    test/CI files, and README references (consistency available on the README
    side).
    """
    return StructuredCodeData(
        files=(
            "src/main.py",
            "README.md",
            "docs/guide.md",
            "tests/test_main.py",
            ".github/workflows/ci.yml",
            "requirements.txt",
        ),
        languages={"python": 3},
        key_files=("src/main.py", "requirements.txt"),
        file_count=6,
        file_contents={
            "requirements.txt": "requests==2.31.0\nflask==2.0.1\n",
            "src/main.py": "def main():\n    pass\n",
        },
    )


def _sparse_structured_data() -> StructuredCodeData:
    """StructuredCodeData that forces categories unavailable.

    No lockfile (dependency missing), no doc files, no test/CI files, and no
    languages/key_files -> several categories are derived from missing data.
    """
    return StructuredCodeData(
        files=("src/main.py",),
        languages={},
        key_files=(),
        file_count=1,
        file_contents={},
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _mock_fetchers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    structured: StructuredCodeData,
) -> dict[str, Mock]:
    """Patch the external fetchers used by ``/analyze`` with canned mocks."""
    get_readme = Mock(return_value=CANNED_README)
    analyze_repository = Mock(return_value=structured)
    explain_repo = Mock(return_value=CANNED_ANALYSIS)
    explain_repo_with_code = Mock(return_value=CANNED_ANALYSIS)

    monkeypatch.setattr(main, "get_readme", get_readme)
    monkeypatch.setattr(main, "analyze_repository", analyze_repository)
    monkeypatch.setattr(main, "explain_repo", explain_repo)
    monkeypatch.setattr(main, "explain_repo_with_code", explain_repo_with_code)

    return {
        "get_readme": get_readme,
        "analyze_repository": analyze_repository,
        "explain_repo": explain_repo,
        "explain_repo_with_code": explain_repo_with_code,
    }


# --------------------------------------------------------------------------- #
# 1. Enabled: composite + five in-range category scores (Req 7.1, 7.2)
# --------------------------------------------------------------------------- #


def test_enabled_returns_composite_and_five_category_scores(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", True)
    _mock_fetchers(monkeypatch, structured=_rich_structured_data())

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    body = resp.json()

    health = body["health"]
    assert health["available"] is True

    # Composite is an integer in [0, 100] (Req 7.2).
    composite = health["composite"]
    assert isinstance(composite, int) and not isinstance(composite, bool)
    assert 0 <= composite <= 100

    # Exactly the five categories, each numeric in range with a name + flag (7.1, 7.2).
    categories = health["categories"]
    assert len(categories) == 5

    expected_names = {
        "README_Quality",
        "Documentation",
        "Test_Coverage",
        "Dependency_Freshness",
        "Readme_Code_Consistency",
    }
    seen_names = set()
    for cat in categories:
        assert isinstance(cat["category"], str)
        seen_names.add(cat["category"])
        score = cat["score"]
        assert isinstance(score, int) and not isinstance(score, bool)
        assert 0 <= score <= 100
        assert isinstance(cat["missing_data"], bool)

    assert seen_names == expected_names


# --------------------------------------------------------------------------- #
# 2. Additive: existing fields unchanged alongside health (Req 7.3)
# --------------------------------------------------------------------------- #


def test_health_block_is_additive_existing_fields_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", True)
    _mock_fetchers(monkeypatch, structured=_rich_structured_data())

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    body = resp.json()

    # Existing fields present and unchanged (Req 7.3).
    assert body["repo"] == "o/r"
    assert body["analysis"] == CANNED_ANALYSIS
    assert body["includes_code"] is False
    # health added on top of the existing shape.
    assert "health" in body


# --------------------------------------------------------------------------- #
# 3. Reuse: no fetches beyond the existing analysis flow (Req 7.4)
# --------------------------------------------------------------------------- #


def test_scoring_reuses_existing_fetches_no_extra_calls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", True)
    mocks = _mock_fetchers(monkeypatch, structured=_rich_structured_data())

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    # Scoring must not issue new external analysis calls (Req 7.4): the README
    # and code-structure data are each fetched exactly once for the request.
    assert mocks["get_readme"].call_count == 1
    assert mocks["analyze_repository"].call_count == 1


# --------------------------------------------------------------------------- #
# 4. Missing data surfaces a per-category indicator (Req 7.5)
# --------------------------------------------------------------------------- #


def test_missing_data_surfaces_per_category_indicator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", True)
    _mock_fetchers(monkeypatch, structured=_sparse_structured_data())

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    body = resp.json()

    categories = body["health"]["categories"]
    # At least one category is marked as derived from missing data (Req 7.5).
    assert any(cat["missing_data"] is True for cat in categories)


# --------------------------------------------------------------------------- #
# 5. Failure path: existing fields + health.available False with a reason (Req 7.6)
# --------------------------------------------------------------------------- #


def test_failure_path_preserves_fields_and_reports_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", True)
    _mock_fetchers(monkeypatch, structured=_rich_structured_data())

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced extraction failure")

    # Force signal extraction to fail; scoring can never run (Req 7.6).
    monkeypatch.setattr(main, "extract_signals", _boom)

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    body = resp.json()

    # Existing fields preserved despite the failure (Req 7.6).
    assert body["repo"] == "o/r"
    assert body["analysis"] == CANNED_ANALYSIS
    assert body["includes_code"] is False

    # Health marked unavailable with a non-empty reason.
    health = body["health"]
    assert health["available"] is False
    assert isinstance(health["reason"], str) and health["reason"].strip()
    assert "composite" not in health
    assert "categories" not in health


# --------------------------------------------------------------------------- #
# 6. Disabled (backward compat): no health key, original shape
# --------------------------------------------------------------------------- #


def test_disabled_omits_health_and_keeps_original_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default disabled; set explicitly for clarity and auto-restore safety.
    monkeypatch.setattr(main, "HEALTH_SCORING_ENABLED", False)
    mocks = _mock_fetchers(monkeypatch, structured=_rich_structured_data())

    resp = client.get("/analyze", params={"owner": "o", "repo": "r"})

    assert resp.status_code == 200
    body = resp.json()

    # Original response shape with no health key (backward compatible).
    assert set(body.keys()) == {"repo", "analysis", "includes_code"}
    assert body["repo"] == "o/r"
    assert body["analysis"] == CANNED_ANALYSIS
    assert body["includes_code"] is False

    # With scoring disabled and include_code False, analyze_repository is not
    # needed at all — confirming no code structure fetch in the disabled path.
    assert mocks["analyze_repository"].call_count == 0
