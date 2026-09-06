"""Signal extraction for the repo health score.

:func:`extract_signals` transforms already-collected README text and structured
code data (:class:`~app.health.models.StructuredCodeData`) into a
:class:`~app.health.models.RepositorySignals` object consumed by the scoring
core. It is part of the **pure core**: it imports only the standard library and
``app.health`` local modules and issues **no external network request**
(Req 1.5, 3, 4.5).

Responsibilities implemented here (task 5.1 — the core):

- Classify detected files into documentation, test, CI_Config, and lockfile
  groups by path pattern (Req 1.3).
- Populate ``languages``, ``key_files`` and ``lockfiles`` from the *structured*
  fields of :class:`StructuredCodeData` — never by parsing the formatted text
  summary (Req 1.4).
- Compute :class:`~app.health.models.ReadmeMetrics` (length, heading count,
  code-block count, install/usage section presence) from the README text.
- Set per-category availability flags; when README or code data is absent,
  populate empty collections/strings and set the affected flags false
  (Req 1.6/1.7).

Seams for later tasks (kept deliberately isolated so they can be completed
without conflicting with this core):

- **Task 5.2** — README-vs-code consistency derivation. This module currently
  sets ``consistency_ratio = 0.0`` and derives ``consistency_available`` from a
  placeholder helper (:func:`_derive_consistency`). 5.2 replaces that helper's
  body with real tokenization / matching (Req 3).
- **Task 5.3** — dependency pinning aggregation across lockfiles. This module
  currently sets ``dependency_total = 0`` / ``dependency_unpinned = 0`` via the
  placeholder helper (:func:`_aggregate_dependencies`). 5.3 replaces that
  helper's body to call :func:`app.health.lockfile.parse_pinning` per lockfile
  and sum the results (Req 4.4).

  Note: ``dependency_available`` (Req 4.6) is derived from lockfile *presence*
  here and is correct as-is; 5.3 only needs to fill in the counts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from app.health.lockfile import parse_pinning
from app.health.models import (
    ReadmeMetrics,
    RepositorySignals,
    StructuredCodeData,
)

__all__ = ["extract_signals"]


# --------------------------------------------------------------------------- #
# Known language names (kept in sync with code_analyzer.LANGUAGE_EXTENSIONS).
# Duplicated here rather than imported to keep the pure core free of any
# dependency on the network-bound ``code_analyzer`` module.
# --------------------------------------------------------------------------- #
KNOWN_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "java",
        "csharp",
        "ruby",
        "go",
        "rust",
        "cpp",
        "c",
        "html",
        "css",
        "sql",
        "json",
        "yaml",
        "markdown",
    }
)

# Recognized dependency-manifest / lock file basenames. Mirrors the dispatch
# names supported by ``app.health.lockfile.parse_pinning`` plus ``setup.py``
# (Task 5.1 detail / Req 1.3, 4.1).
LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "go.mod",
        "cargo.toml",
    }
)


# --------------------------------------------------------------------------- #
# Group A technology-reference terms (Task 14.3, Req 3.1/3.3/3.4).
#
# Each README term maps to a concrete *file-presence* detector over the
# structured file tree (``StructuredCodeData.files``). A Group A term counts as
# a consistency **match** ONLY when its detector finds the corresponding
# artifact in the file tree; otherwise the term is a **mismatch** (never a
# vacuous match). All detectors are pure and offline (Req 3, 1.5, 4.5).
#
# Group B (framework/ecosystem terms parsed from manifest *contents*, e.g.
# React/Django/FastAPI) is intentionally OUT OF SCOPE here and deferred to a
# future requirement — this module does not parse manifest dependency lists for
# consistency.
# --------------------------------------------------------------------------- #


def _has_basename(files: Iterable[str], *names: str) -> bool:
    """True when the file tree contains any file whose basename is in ``names``."""
    wanted = {n.lower() for n in names}
    return any(_basename(path) in wanted for path in files)


def _has_dir_segment(files: Iterable[str], *segments: str) -> bool:
    """True when any path contains one of ``segments`` as an intermediate dir."""
    wanted = {s.lower() for s in segments}
    return any(any(seg in wanted for seg in _path_segments(path)[:-1]) for path in files)


def _has_extension(files: Iterable[str], *extensions: str) -> bool:
    """True when the file tree contains any file with one of ``extensions``."""
    suffixes = tuple(e.lower() for e in extensions)
    return any(_basename(path).endswith(suffixes) for path in files)


def _detect_docker(files: tuple[str, ...]) -> bool:
    return _has_basename(files, "dockerfile")


def _detect_docker_compose(files: tuple[str, ...]) -> bool:
    return _has_basename(
        files,
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    )


def _detect_kubernetes(files: tuple[str, ...]) -> bool:
    return (
        _has_dir_segment(files, "k8s", "kubernetes")
        or _has_basename(files, "chart.yaml")
    )


def _detect_helm(files: tuple[str, ...]) -> bool:
    return _has_basename(files, "chart.yaml") or _has_dir_segment(files, "helm")


def _detect_terraform(files: tuple[str, ...]) -> bool:
    return _has_extension(files, ".tf")


def _detect_makefile(files: tuple[str, ...]) -> bool:
    return _has_basename(files, "makefile")


def _detect_graphql(files: tuple[str, ...]) -> bool:
    return _has_extension(files, ".graphql", ".gql")


def _detect_github_actions(files: tuple[str, ...]) -> bool:
    return any(".github/workflows/" in path.lower() for path in files)


# Maps a README term (lowercase) to its code-side file-presence detector.
# Synonyms share the same detector (design "Group A vocabulary table").
TECH_REFERENCE_TERMS: dict[str, Callable[[tuple[str, ...]], bool]] = {
    "docker": _detect_docker,
    "dockerfile": _detect_docker,
    "docker-compose": _detect_docker_compose,
    "compose": _detect_docker_compose,
    "kubernetes": _detect_kubernetes,
    "k8s": _detect_kubernetes,
    "helm": _detect_helm,
    "terraform": _detect_terraform,
    "make": _detect_makefile,
    "makefile": _detect_makefile,
    "graphql": _detect_graphql,
    "github-actions": _detect_github_actions,
    "workflow": _detect_github_actions,
}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def extract_signals(
    readme: str | None,
    code: StructuredCodeData | None,
) -> RepositorySignals:
    """Build a :class:`RepositorySignals` from README text and structured code data.

    Reads only already-collected inputs and structured fields (Req 1.1, 1.4);
    performs no network request (Req 1.5). When ``readme`` is ``None``/empty or
    ``code`` is ``None``, the affected fields are populated with empty
    collections/strings and their availability flags set to ``False``
    (Req 1.6/1.7).
    """
    # --- README-derived fields (Req 1.6/1.7) --- #
    readme_text = readme or ""
    readme_available = bool(readme_text.strip())
    readme_metrics = _compute_readme_metrics(readme_text)

    # --- Structured code-derived fields (Req 1.2, 1.4) --- #
    # NOTE: languages, key_files, and file paths come strictly from the
    # STRUCTURED fields of StructuredCodeData — never by parsing formatted text.
    if code is None:
        files: tuple[str, ...] = ()
        languages: dict[str, int] = {}
        key_files: tuple[str, ...] = ()
    else:
        files = tuple(code.files)
        languages = dict(code.languages)
        key_files = tuple(code.key_files)

    # --- File classification by path pattern (Req 1.3) --- #
    doc_files = tuple(p for p in files if _is_documentation_file(p))
    test_files = tuple(p for p in files if _is_test_file(p))
    ci_files = tuple(p for p in files if _is_ci_config_file(p))
    lockfiles = tuple(p for p in files if _is_lockfile(p))

    # --- Availability flags (Req 1.6/1.7, 4.6) --- #
    code_available = code is not None
    documentation_available = code_available and bool(doc_files)
    test_coverage_available = code_available and bool(test_files or ci_files)
    dependency_available = code_available and bool(lockfiles)

    # --- Dependency pinning aggregation (Req 4.4) --- #
    # SEAM (Task 5.3): fill in the aggregated pinning counts. Availability is
    # already derived from lockfile presence above.
    dependency_total, dependency_unpinned = _aggregate_dependencies(
        lockfiles,
        code.file_contents if code is not None else {},
    )

    # --- README-vs-code consistency (Req 3) --- #
    consistency_ratio, consistency_available = _derive_consistency(
        readme_text,
        languages,
        key_files,
        files,
    )

    return RepositorySignals(
        readme_text=readme_text,
        readme_metrics=readme_metrics,
        doc_files=doc_files,
        test_files=test_files,
        ci_files=ci_files,
        lockfiles=lockfiles,
        languages=languages,
        key_files=key_files,
        dependency_total=dependency_total,
        dependency_unpinned=dependency_unpinned,
        consistency_ratio=consistency_ratio,
        readme_available=readme_available,
        documentation_available=documentation_available,
        test_coverage_available=test_coverage_available,
        dependency_available=dependency_available,
        consistency_available=consistency_available,
    )


# --------------------------------------------------------------------------- #
# README metrics (Req 2.1 completeness signals)
# --------------------------------------------------------------------------- #

# Markdown ATX headings: 1-6 leading '#'s followed by a space.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)
# Fenced code blocks delimited by ``` or ~~~ (count opening fences).
_CODE_FENCE_RE = re.compile(r"^\s*(?:```|~~~)", re.MULTILINE)
# Section keywords, matched case-insensitively anywhere in the text.
_INSTALL_RE = re.compile(r"install(?:ation|ing)?", re.IGNORECASE)
_USAGE_RE = re.compile(r"\b(?:usage|getting started|quick\s*start|how to use)\b", re.IGNORECASE)


def _compute_readme_metrics(readme_text: str) -> ReadmeMetrics:
    """Compute completeness metrics from README text.

    An empty README yields all-zero / all-false metrics.
    """
    if not readme_text:
        return ReadmeMetrics(
            length=0,
            heading_count=0,
            code_block_count=0,
            has_install_section=False,
            has_usage_section=False,
        )

    length = len(readme_text)
    heading_count = len(_HEADING_RE.findall(readme_text))
    # Each code block is a pair of fences; count opening fences (floor of pairs).
    fence_count = len(_CODE_FENCE_RE.findall(readme_text))
    code_block_count = fence_count // 2
    has_install_section = bool(_INSTALL_RE.search(readme_text))
    has_usage_section = bool(_USAGE_RE.search(readme_text))

    return ReadmeMetrics(
        length=length,
        heading_count=heading_count,
        code_block_count=code_block_count,
        has_install_section=has_install_section,
        has_usage_section=has_usage_section,
    )


# --------------------------------------------------------------------------- #
# File classification by path pattern (Req 1.3)
# --------------------------------------------------------------------------- #


def _basename(path: str) -> str:
    """Return the lowercase final path component."""
    return path.rsplit("/", 1)[-1].strip().lower()


def _path_segments(path: str) -> list[str]:
    """Return the lowercase '/'-separated segments of a path."""
    return [seg for seg in path.lower().split("/") if seg]


def _is_documentation_file(path: str) -> bool:
    """A documentation file: a Markdown/reStructuredText file or a docs dir entry."""
    name = _basename(path)
    segments = _path_segments(path)
    if name.endswith((".md", ".markdown", ".rst")):
        return True
    # Entries under a documentation directory (docs/, doc/).
    if any(seg in ("docs", "doc") for seg in segments[:-1]):
        return True
    return False


def _is_test_file(path: str) -> bool:
    """A test file: test_* / *_test names, spec files, or a tests/ dir entry."""
    name = _basename(path)
    segments = _path_segments(path)
    # A test/tests/spec/__tests__ directory anywhere in the path.
    if any(seg in ("test", "tests", "spec", "specs", "__tests__") for seg in segments[:-1]):
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.startswith("test_") or stem.startswith("test-"):
        return True
    if stem.endswith("_test") or stem.endswith("-test"):
        return True
    # JS/TS style: foo.test.js, foo.spec.ts
    if ".test." in name or ".spec." in name:
        return True
    if stem.endswith(".test") or stem.endswith(".spec"):
        return True
    return False


def _is_ci_config_file(path: str) -> bool:
    """A CI configuration artifact detectable from the file tree (Req 1.3)."""
    lower = path.lower()
    name = _basename(path)
    segments = _path_segments(path)
    # GitHub Actions workflows: .github/workflows/*.yml
    if ".github/workflows/" in lower:
        return True
    # CircleCI: .circleci/ directory
    if any(seg == ".circleci" for seg in segments):
        return True
    # Well-known single-file CI configs.
    if name in (
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        ".appveyor.yml",
        "appveyor.yml",
        "jenkinsfile",
        ".drone.yml",
        "bitbucket-pipelines.yml",
    ):
        return True
    return False


def _is_lockfile(path: str) -> bool:
    """A dependency manifest / lock file (Req 1.3, 4.1)."""
    return _basename(path) in LOCKFILE_NAMES


# --------------------------------------------------------------------------- #
# Seams for later tasks
# --------------------------------------------------------------------------- #


def _aggregate_dependencies(
    lockfiles: tuple[str, ...],
    file_contents: dict[str, str],
) -> tuple[int, int]:
    """Aggregate ``(dependency_total, dependency_unpinned)`` across lockfiles.

    For each detected lockfile path, its content is looked up in
    ``file_contents`` (keyed by path). When the content is present, it is passed
    to :func:`app.health.lockfile.parse_pinning`, whose returned
    :class:`~app.health.models.DependencyPinning` ``total`` / ``unpinned`` are
    added to the running sums (Req 4.4). A lockfile whose content is not
    available in ``file_contents`` ("present but contents unavailable") is the
    rare fallback and contributes ``(0, 0)`` (Req 4.6 / design error handling).

    ``dependency_available`` is derived from lockfile *presence* in
    :func:`extract_signals` (Req 4.6) and is not affected by this function.
    """
    dependency_total = 0
    dependency_unpinned = 0

    for path in lockfiles:
        content = file_contents.get(path)
        if content is None:
            # Present but contents unavailable — the rare fallback (0, 0).
            continue
        pinning = parse_pinning(path, content)
        dependency_total += pinning.total
        dependency_unpinned += pinning.unpinned

    return dependency_total, dependency_unpinned


# README tokenizer. A token is a run of characters that can form a language
# name (e.g. ``python``, ``c++`` -> ``cpp`` is a known *name* not a token, so we
# keep letters/digits/+/#) or a dotted key-file basename (e.g. ``package.json``,
# ``go.mod``, ``cargo.toml``). We therefore keep letters, digits, and the
# internal separators ``. _ - + #`` while splitting on everything else, then
# strip any leading/trailing separators left over from surrounding punctuation.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+#-]*")


def _tokenize_readme(readme_text: str) -> list[str]:
    """Return lowercase word-like tokens from README text (Req 3.1)."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(readme_text):
        token = raw.strip("._-").lower()
        if token:
            tokens.append(token)
    return tokens


def _derive_consistency(
    readme_text: str,
    languages: dict[str, int],
    key_files: tuple[str, ...],
    files: tuple[str, ...],
) -> tuple[float, bool]:
    """Derive ``(consistency_ratio, consistency_available)`` from README vs code.

    Tokenizes ``readme_text`` and matches tokens case-insensitively against the
    **Technology_Reference_Vocabulary** — :data:`KNOWN_LANGUAGES`, the basenames
    of the detected ``key_files``, and the Group A file-type/config indicators in
    :data:`TECH_REFERENCE_TERMS` (Req 3.1). The matched references are
    deduplicated case-insensitively into a set of unique detectable references
    (Req 3.2).

    Each unique reference is a **match** when it corresponds to:

    - a detected language (a key of ``languages``), or
    - a detected key-file basename, or
    - a Group A term whose file-presence detector finds the corresponding
      artifact in ``files`` (Req 3.3);

    otherwise it is a **mismatch** (Req 3.4). In particular a Group A term whose
    detector does NOT find its artifact is a mismatch, never a vacuous match.
    ``consistency_ratio`` is ``matches / total_unique`` in ``[0.0, 1.0]``
    (Req 3.5).

    ``consistency_available`` is ``False`` when the README has no detectable
    references (Req 3.6) or the code structure has no languages and no key files
    (Req 3.7); otherwise ``True``.
    """
    # Detected code vocabulary (lowercased). Key files arrive as paths; the
    # reference vocabulary and the match test both use their basenames (3.1).
    detected_languages = {lang.lower() for lang in languages}
    detected_key_files = {_basename(path) for path in key_files if _basename(path)}

    # Availability guard on the code side (Req 3.7): no languages AND no key
    # files means there is nothing to be consistent with.
    if not detected_languages and not detected_key_files:
        return 0.0, False

    # The full detection vocabulary (Req 3.1): system-known languages, the code's
    # own detected key-file basenames, and the Group A tech-reference terms.
    detection_vocabulary = (
        KNOWN_LANGUAGES | detected_key_files | frozenset(TECH_REFERENCE_TERMS)
    )

    # Unique detectable references, deduplicated case-insensitively (Req 3.2).
    references = {
        token for token in _tokenize_readme(readme_text) if token in detection_vocabulary
    }

    # Availability guard on the README side (Req 3.6): no detectable references.
    if not references:
        return 0.0, False

    # Partition into matches / mismatches (Req 3.3/3.4). A reference matches when
    # it corresponds to a detected language, a detected key file, or a Group A
    # term whose file-presence detector finds its artifact in the file tree.
    matches = sum(
        1
        for ref in references
        if ref in detected_languages
        or ref in detected_key_files
        or (ref in TECH_REFERENCE_TERMS and TECH_REFERENCE_TERMS[ref](files))
    )
    total = len(references)

    # total >= 1 here (references is non-empty), so the ratio is well-defined.
    ratio = matches / total
    return ratio, True
