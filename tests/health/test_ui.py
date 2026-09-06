"""Example tests for the dashboard render helper ``build_health_view`` (task 11.2).

``build_health_view(health: dict | None) -> dict`` in ``app/ui.py`` is a pure
(no ``st.*`` side effects) mapping from the API's ``health`` block to a
render-ready view. These tests exercise it directly.

Importing ``app.ui`` pulls in streamlit and runs its module-level ``st.*`` calls,
which emit benign "missing ScriptRunContext" warnings in a bare (non-Streamlit)
process. The import still succeeds, so importing the helper is safe.

Covered acceptance criteria:

- Available payload: composite plus the full five-category breakdown in one view
  (Req 8.1, 8.2, 8.3).
- Limited-data indicator: ``missing_data`` categories yield ``limited_data`` true (Req 8.4).
- Unavailable payload: unavailability reason and NO scores (Req 8.5).
- None/disabled: nothing to render.
"""

from __future__ import annotations

from app.ui import build_health_view


# The five categories the API returns, matching the scoring model. Each entry is
# a representative in-range score so the breakdown assertions are meaningful.
_FIVE_CATEGORIES = [
    {"category": "README_Quality", "score": 90, "missing_data": False},
    {"category": "Documentation", "score": 75, "missing_data": False},
    {"category": "Test_Coverage", "score": 60, "missing_data": False},
    {"category": "Dependency_Freshness", "score": 82, "missing_data": False},
    {"category": "Readme_Code_Consistency", "score": 88, "missing_data": False},
]


def test_available_payload_produces_composite_and_full_breakdown():
    """Req 8.1, 8.2, 8.3: an available payload yields the composite and all five
    category rows (name + score) together in one view."""
    health = {
        "available": True,
        "composite": 78,
        "categories": _FIVE_CATEGORIES,
    }

    view = build_health_view(health)

    assert view["state"] == "available"
    # Req 8.1: the composite is surfaced as the integer the API returned.
    assert view["composite"] == 78

    # Req 8.2, 8.3: all five categories are present in the same view, each with
    # its category name and score.
    rows = view["rows"]
    assert len(rows) == 5

    expected_scores = {c["category"]: c["score"] for c in _FIVE_CATEGORIES}
    assert {row["category"] for row in rows} == set(expected_scores)
    for row in rows:
        assert row["score"] == expected_scores[row["category"]]
        # A human-friendly label is derived for display.
        assert row["label"]


def test_missing_data_category_flags_limited_data():
    """Req 8.4: a category with ``missing_data: True`` is flagged limited_data,
    while ``missing_data: False`` is not."""
    health = {
        "available": True,
        "composite": 40,
        "categories": [
            {"category": "README_Quality", "score": 0, "missing_data": True},
            {"category": "Documentation", "score": 70, "missing_data": False},
        ],
    }

    view = build_health_view(health)

    rows_by_category = {row["category"]: row for row in view["rows"]}
    assert rows_by_category["README_Quality"]["limited_data"] is True
    assert rows_by_category["Documentation"]["limited_data"] is False


def test_unavailable_payload_surfaces_reason_and_no_scores():
    """Req 8.5: an unavailable payload yields the reason and NO composite/rows."""
    reason = "Signal extraction failed: README could not be fetched."
    health = {"available": False, "reason": reason}

    view = build_health_view(health)

    assert view["state"] == "unavailable"
    assert view["reason"] == reason
    assert "composite" not in view
    assert "rows" not in view


def test_none_payload_produces_none_state():
    """Scoring disabled/absent: nothing health-related should be rendered."""
    view = build_health_view(None)

    assert view["state"] == "none"
    assert "composite" not in view
    assert "rows" not in view
