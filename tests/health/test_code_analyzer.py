"""Unit tests for the code_analyzer refactor (task 9.4).

These tests lock down two guarantees introduced by the ``code_analyzer``
refactor, WITHOUT touching the network:

- **Guaranteed lockfile fetch (Req 4.1 / 1.4 / 4.4)** — a detected lockfile is
  ALWAYS retained in ``StructuredCodeData.files`` even when it appears beyond
  the ``max_files`` cap, and its content is ALWAYS present in
  ``StructuredCodeData.file_contents`` (a separate, additional fetch).

- **Summary compatibility (Req 1.4 / 7.4)** — ``get_code_summary`` produces the
  same free-text summary the AI path historically consumed, byte-for-byte with
  the ``_format_code_summary`` output for the same structured data.

Mocking seam
------------
``get_repository_tree`` and ``get_file_content`` both route through
``app.code_analyzer.requests.get``, and the tree scan first resolves the
default branch via ``get_repository_default_branch``. We monkeypatch:

- ``app.code_analyzer.get_repository_default_branch`` -> a fixed branch, and
- ``app.code_analyzer.requests.get`` -> a canned dispatcher that returns the
  git-tree listing for the ``git/trees`` endpoint and base64 file content for
  the ``contents`` endpoint.

A sentinel is also installed so that any *unexpected* URL raises loudly,
guaranteeing no real network access can slip through.
"""

from __future__ import annotations

import base64

import pytest

import app.code_analyzer as ca
from app.code_analyzer import (
    analyze_repository,
    get_code_summary,
    _format_code_summary,
)
from app.health.models import StructuredCodeData


# --------------------------------------------------------------------------- #
# Fake GitHub transport
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Minimal stand-in for a ``requests.Response`` object."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict:
        return self._payload


def _tree_payload(paths: list[str]) -> dict:
    """Build a GitHub git-tree API payload from a list of blob paths."""
    return {"tree": [{"type": "blob", "path": p} for p in paths]}


def _content_payload(text: str) -> dict:
    """Build a GitHub contents API payload with base64-encoded content."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"content": encoded}


def _install_fake_github(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tree: list[str],
    contents: dict[str, str],
    branch: str = "main",
) -> dict[str, int]:
    """Patch the code_analyzer network seam with canned responses.

    Returns a mutable ``calls`` dict recording how many times each endpoint kind
    was hit, so tests can assert the guaranteed lockfile fetch actually happened.
    """
    calls = {"tree": 0, "content": 0}

    monkeypatch.setattr(
        ca, "get_repository_default_branch", lambda owner, repo: branch
    )

    def _fake_get(url: str, *args: object, **kwargs: object) -> _FakeResponse:
        if "/git/trees/" in url:
            calls["tree"] += 1
            return _FakeResponse(_tree_payload(tree))
        if "/contents/" in url:
            calls["content"] += 1
            # Extract the file path after ".../contents/".
            file_path = url.split("/contents/", 1)[1]
            if file_path in contents:
                return _FakeResponse(_content_payload(contents[file_path]))
            # Missing content -> payload without "content" key (fetch "failure").
            return _FakeResponse({})
        raise AssertionError(f"unexpected network URL: {url}")

    monkeypatch.setattr(ca.requests, "get", _fake_get)
    return calls


# --------------------------------------------------------------------------- #
# 1. Guaranteed lockfile retention beyond the cap (Req 4.1)
# --------------------------------------------------------------------------- #


def test_lockfile_retained_beyond_max_files_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lockfile beyond ``max_files`` is still present in ``files`` (Req 4.1).

    The tree lists several non-lockfile important files BEFORE a lockfile
    (``requirements.txt``). With ``max_files=2`` the non-lockfile files are
    capped at two, so the lockfile falls "beyond the cap" — yet it must be
    retained in the returned structured data.
    """
    tree = [
        "src/main.py",       # important (non-lockfile) #1
        "src/server.py",     # important (non-lockfile) #2  -> cap reached here
        "src/config.py",     # important (non-lockfile) #3  -> dropped by cap
        "src/extra.py",      # important (non-lockfile) #4  -> dropped by cap
        "requirements.txt",  # lockfile beyond the cap -> MUST be retained
    ]
    contents = {
        "src/main.py": "print('main')",
        "src/server.py": "print('server')",
        "requirements.txt": "requests==2.31.0\n",
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=2)

    assert isinstance(data, StructuredCodeData)
    # The lockfile survived the cap.
    assert "requirements.txt" in data.files
    # The non-lockfile cap actually took effect (a dropped file is absent),
    # proving the lockfile's retention is due to the guarantee, not the cap.
    assert "src/extra.py" not in data.files


# --------------------------------------------------------------------------- #
# 2. Guaranteed lockfile content fetched (Req 4.4 / 1.4)
# --------------------------------------------------------------------------- #


def test_lockfile_content_present_in_file_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retained lockfile's CONTENT is fetched into ``file_contents`` (Req 4.4).

    The lockfile is not a "key file" (it does not match the key-file heuristic),
    so its content is present ONLY because of the guaranteed additional fetch.
    """
    tree = [
        "src/main.py",
        "src/server.py",
        "src/config.py",
        "src/extra.py",
        "requirements.txt",
    ]
    lockfile_body = "requests==2.31.0\nflask>=2.0\n"
    contents = {
        "src/main.py": "print('main')",
        "src/server.py": "print('server')",
        "requirements.txt": lockfile_body,
    }
    calls = _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=2)

    # Content of the lockfile was fetched via the guaranteed additional fetch.
    assert data.file_contents.get("requirements.txt") == lockfile_body
    # At least one contents fetch occurred (the lockfile fetch happened).
    assert calls["content"] >= 1


def test_lockfile_not_a_key_file_still_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lockfile that is NOT a key file is fetched purely by the guarantee.

    ``go.mod`` does not match the key-file name heuristic, so it would never be
    fetched as a key-file sample. It must still land in ``file_contents``.
    """
    tree = ["src/handler.py", "go.mod"]
    go_mod = "module example.com/m\n\nrequire example.com/x v1.2.3\n"
    contents = {
        "src/handler.py": "print('handler')",
        "go.mod": go_mod,
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=20)

    assert "go.mod" not in data.key_files  # confirms it is not a key file
    assert data.file_contents.get("go.mod") == go_mod


# --------------------------------------------------------------------------- #
# 3. get_code_summary byte-for-byte compatibility (Req 1.4 / 7.4)
# --------------------------------------------------------------------------- #


def test_get_code_summary_matches_format_of_structured_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_code_summary output equals _format_code_summary of the same data.

    ``get_code_summary`` is a thin adapter: it derives ``analyze_repository``
    output once and formats it. Its output MUST be byte-for-byte the legacy
    free-text summary, which we pin by comparing against ``_format_code_summary``
    applied to the identical structured data.
    """
    tree = [
        "src/main.py",
        "src/server.py",
        "src/config.py",
        "README.md",
        "requirements.txt",
    ]
    contents = {
        "src/main.py": "def main():\n    pass\n",
        "src/server.py": "def serve():\n    pass\n",
        "src/config.py": "SETTING = 1\n",
        "requirements.txt": "requests==2.31.0\n",
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    # Build the structured data with the SAME mocked transport, then format it.
    data = analyze_repository("owner", "repo", max_files=20)
    expected = _format_code_summary(data)

    summary = get_code_summary("owner", "repo")

    assert summary == expected


def test_get_code_summary_has_legacy_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_code_summary carries the legacy sections and a sample block.

    Complements the byte-for-byte check with an explicit structural assertion so
    a regression in the section layout is caught with a readable failure.
    """
    tree = ["src/main.py", "src/config.py", "requirements.txt"]
    contents = {
        "src/main.py": "def main():\n    return 42\n",
        "src/config.py": "SETTING = 1\n",
        "requirements.txt": "requests==2.31.0\n",
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    summary = get_code_summary("owner", "repo")

    assert summary.lstrip().startswith("Repository Structure Analysis:")
    assert "Languages detected:" in summary
    assert "Key files:" in summary
    assert "File List:" in summary
    assert "Code Samples (first 500 chars of key files):" in summary
    # A sample block for a fetched key file is present.
    assert "--- src/main.py (python) ---" in summary
    assert "def main():" in summary


def test_get_code_summary_empty_tree_returns_no_source_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty tree yields the legacy 'No source files found.' sentinel."""
    _install_fake_github(monkeypatch, tree=[], contents={})

    summary = get_code_summary("owner", "repo")

    assert summary == "No source files found."


# --------------------------------------------------------------------------- #
# 4. Classification-relevant retention beyond the content budget (task 14.1/14.2)
#
# Documentation (Markdown / ``docs/``), test, and CI paths are classification-
# relevant. They must be retained in ``StructuredCodeData.files`` even when they
# appear beyond the ``max_files`` content-fetch budget and even when
# ``is_important_file`` would exclude them (Markdown is excluded). ``max_files``
# bounds only how many non-lockfile, non-classification file CONTENTS are
# fetched for the AI summary — not which paths appear in ``files`` (Req 1.8/1.9).
# --------------------------------------------------------------------------- #


def test_markdown_documentation_retained_despite_is_important_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdown docs are retained in ``files`` even though ``is_important_file``
    excludes them (regression: Markdown was previously dropped, false-zeroing
    Documentation).
    """
    tree = ["src/main.py", "README.md", "docs/guide.md", "docs/api.rst"]
    contents = {"src/main.py": "print('hi')"}
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=20)

    # Markdown / docs entries are excluded by is_important_file, yet retained.
    assert not ca.is_important_file("README.md")
    assert "README.md" in data.files
    assert "docs/guide.md" in data.files
    assert "docs/api.rst" in data.files


def test_classification_paths_retained_beyond_content_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc / test / CI paths beyond the ``max_files`` cap are still in ``files``.

    The tree lists enough important non-classification files to exhaust a tiny
    ``max_files`` budget BEFORE the classification-relevant paths appear. Those
    later doc/test/CI paths must still be retained, because the budget bounds
    only the AI content-sample selection, not the classification set (Req 1.9).
    """
    tree = [
        "src/main.py",              # important (non-classification) #1
        "src/server.py",            # important (non-classification) #2 -> cap hit
        "src/config.py",            # important (non-classification) #3 -> dropped
        "src/extra.py",             # important (non-classification) #4 -> dropped
        "docs/guide.md",            # documentation -> retained beyond cap
        "tests/test_core.py",       # test -> retained beyond cap
        ".github/workflows/ci.yml", # CI config -> retained beyond cap
    ]
    contents = {
        "src/main.py": "print('main')",
        "src/server.py": "print('server')",
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=2)

    # The non-classification cap actually took effect (a dropped file is absent).
    assert "src/extra.py" not in data.files
    # Classification-relevant paths survive the cap.
    assert "docs/guide.md" in data.files
    assert "tests/test_core.py" in data.files
    assert ".github/workflows/ci.yml" in data.files


def test_get_code_summary_unchanged_with_classification_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_code_summary`` stays byte-for-byte the ``_format_code_summary``
    output even when the tree contains newly-retained documentation / test / CI
    paths (task 14.5: summary output must not change).
    """
    tree = [
        "src/main.py",
        "src/server.py",
        "README.md",
        "docs/guide.md",
        "tests/test_core.py",
        ".github/workflows/ci.yml",
        "requirements.txt",
    ]
    contents = {
        "src/main.py": "def main():\n    pass\n",
        "src/server.py": "def serve():\n    pass\n",
        "requirements.txt": "requests==2.31.0\n",
    }
    _install_fake_github(monkeypatch, tree=tree, contents=contents)

    data = analyze_repository("owner", "repo", max_files=20)
    expected = _format_code_summary(data)

    summary = get_code_summary("owner", "repo")

    assert summary == expected
