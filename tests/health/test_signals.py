"""Unit tests for app.health.signals.extract_signals (task 5.13).

Focus of this file:

- Structured, not textual: ``extract_signals`` populates ``languages``,
  ``key_files`` and ``lockfiles`` from the STRUCTURED fields of
  :class:`StructuredCodeData` — it never parses a formatted free-text summary
  (Req 1.4).
- Empty inputs: an absent README and/or absent code data yields empty
  collections/strings and per-category availability flags set to ``False``
  (Req 1.6). Empty files/languages/key_files collections likewise flip the
  affected availability flags off.

These are example-based unit tests; the universal properties are covered
separately in tests/health/test_properties.py.
"""

from __future__ import annotations

from app.health.models import RepositorySignals, StructuredCodeData
from app.health.signals import extract_signals


# --------------------------------------------------------------------------- #
# Both inputs absent (Req 1.6)
# --------------------------------------------------------------------------- #


def test_extract_signals_none_none_is_all_empty_and_unavailable() -> None:
    """extract_signals(None, None): empty everything, all flags False."""
    signals = extract_signals(None, None)

    assert isinstance(signals, RepositorySignals)

    # README-derived: empty text, zeroed metrics.
    assert signals.readme_text == ""
    assert signals.readme_metrics.length == 0
    assert signals.readme_metrics.heading_count == 0
    assert signals.readme_metrics.code_block_count == 0
    assert signals.readme_metrics.has_install_section is False
    assert signals.readme_metrics.has_usage_section is False

    # Code-derived collections empty.
    assert signals.doc_files == ()
    assert signals.test_files == ()
    assert signals.ci_files == ()
    assert signals.lockfiles == ()
    assert signals.languages == {}
    assert signals.key_files == ()

    # Dependency / consistency defaults.
    assert signals.dependency_total == 0
    assert signals.dependency_unpinned == 0
    assert signals.consistency_ratio == 0.0

    # Every availability flag is False.
    assert signals.readme_available is False
    assert signals.documentation_available is False
    assert signals.test_coverage_available is False
    assert signals.dependency_available is False
    assert signals.consistency_available is False


# --------------------------------------------------------------------------- #
# README present, code absent (Req 1.6)
# --------------------------------------------------------------------------- #


def test_extract_signals_readme_only_sets_readme_available_but_no_code_flags() -> None:
    """A README with code=None: readme_available True, code-dependent flags False."""
    readme = (
        "# My Project\n\n"
        "## Installation\n\n"
        "```bash\npip install myproject\n```\n\n"
        "## Usage\n\n"
        "Run it.\n"
    )

    signals = extract_signals(readme, None)

    # README is available and its metrics are populated.
    assert signals.readme_available is True
    assert signals.readme_text == readme
    assert signals.readme_metrics.length == len(readme)
    assert signals.readme_metrics.heading_count >= 1
    assert signals.readme_metrics.code_block_count == 1
    assert signals.readme_metrics.has_install_section is True
    assert signals.readme_metrics.has_usage_section is True

    # No code data -> code-derived collections empty.
    assert signals.doc_files == ()
    assert signals.test_files == ()
    assert signals.ci_files == ()
    assert signals.lockfiles == ()
    assert signals.languages == {}
    assert signals.key_files == ()

    # Code-dependent availability flags are False.
    assert signals.documentation_available is False
    assert signals.test_coverage_available is False
    assert signals.dependency_available is False
    # No code languages/key files -> consistency unavailable (Req 3.7).
    assert signals.consistency_available is False


def test_blank_readme_string_is_unavailable() -> None:
    """A whitespace-only README is treated as absent (readme_available False)."""
    signals = extract_signals("   \n\t  ", None)

    assert signals.readme_available is False
    assert signals.readme_metrics.has_install_section is False


# --------------------------------------------------------------------------- #
# Structured fields are read directly — NOT parsed from formatted text (Req 1.4)
# --------------------------------------------------------------------------- #


def test_extract_signals_reads_structured_fields_and_classifies_by_path() -> None:
    """languages/key_files/lockfiles come from StructuredCodeData structured fields.

    We pass explicit ``files``/``languages``/``key_files`` and assert the
    classification and structured passthrough match those fields exactly. The
    ``languages`` dict must be the one carried on StructuredCodeData, never a
    value re-derived from any formatted summary text (Req 1.4).
    """
    files = (
        "src/app.py",  # plain source (unclassified)
        "docs/guide.md",  # documentation (docs/ dir + .md)
        "README.rst",  # documentation (.rst)
        "tests/test_app.py",  # test (tests/ dir + test_ prefix)
        "app_test.py",  # test (_test suffix)
        ".github/workflows/ci.yml",  # CI config
        ".gitlab-ci.yml",  # CI config
        "requirements.txt",  # lockfile
        "package.json",  # lockfile
    )
    languages = {"python": 3, "javascript": 1}
    key_files = ("requirements.txt", "package.json", "src/app.py")

    code = StructuredCodeData(
        files=files,
        languages=languages,
        key_files=key_files,
        file_count=len(files),
        file_contents={},
    )

    signals = extract_signals("", code)

    # languages dict comes STRAIGHT from StructuredCodeData.languages (Req 1.4).
    assert signals.languages == {"python": 3, "javascript": 1}
    assert signals.languages is not languages  # defensive copy, same contents
    # key_files passed through from the structured field.
    assert signals.key_files == key_files

    # Classification by path pattern (Req 1.3).
    assert set(signals.doc_files) == {"docs/guide.md", "README.rst"}
    assert set(signals.test_files) == {"tests/test_app.py", "app_test.py"}
    assert set(signals.ci_files) == {".github/workflows/ci.yml", ".gitlab-ci.yml"}
    assert set(signals.lockfiles) == {"requirements.txt", "package.json"}

    # Code data present with each group non-empty -> flags True.
    assert signals.documentation_available is True
    assert signals.test_coverage_available is True
    assert signals.dependency_available is True


def test_languages_ignores_readme_text_entirely() -> None:
    """The README mentioning other languages does not alter structured languages.

    Proves languages are read from the structured field, not parsed from text:
    the README talks about Rust and Go, but the structured languages dict only
    lists Python, and that is exactly what extract_signals reports.
    """
    readme = "This project is written in Rust and Go and TypeScript.\n"
    code = StructuredCodeData(
        files=("main.py",),
        languages={"python": 1},
        key_files=(),
        file_count=1,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.languages == {"python": 1}


# --------------------------------------------------------------------------- #
# Empty structured collections flip availability flags off (Req 1.6)
# --------------------------------------------------------------------------- #


def test_empty_files_and_languages_and_key_files_reflect_absence() -> None:
    """Empty files tuple, empty languages, empty key_files -> flags reflect absence."""
    code = StructuredCodeData(
        files=(),
        languages={},
        key_files=(),
        file_count=0,
        file_contents={},
    )

    signals = extract_signals("Some readme text with no tech references.\n", code)

    assert signals.doc_files == ()
    assert signals.test_files == ()
    assert signals.ci_files == ()
    assert signals.lockfiles == ()
    assert signals.languages == {}
    assert signals.key_files == ()

    # code is not None, but every classified group is empty -> code flags False.
    assert signals.documentation_available is False
    assert signals.test_coverage_available is False
    assert signals.dependency_available is False
    # No languages and no key files -> consistency unavailable (Req 3.7).
    assert signals.consistency_available is False


# --------------------------------------------------------------------------- #
# Dependency availability + aggregation from a present lockfile (Req 4.4 / 4.6)
# --------------------------------------------------------------------------- #


def test_lockfile_with_contents_yields_dependency_available_and_counts() -> None:
    """A lockfile with content in file_contents -> dependency_available + counts.

    requirements.txt declares one pinned (==) and one unpinned (>=) dependency,
    so aggregated total=2, unpinned=1.
    """
    requirements = "flask==2.0.1\nrequests>=2.0\n"
    code = StructuredCodeData(
        files=("requirements.txt",),
        languages={"python": 1},
        key_files=("requirements.txt",),
        file_count=1,
        file_contents={"requirements.txt": requirements},
    )

    signals = extract_signals("", code)

    assert signals.lockfiles == ("requirements.txt",)
    assert signals.dependency_available is True
    assert signals.dependency_total == 2
    assert signals.dependency_unpinned == 1


def test_no_lockfile_means_dependency_unavailable_and_zero_counts() -> None:
    """No detected lockfile -> dependency_available False and zero counts."""
    code = StructuredCodeData(
        files=("main.py", "docs/guide.md"),
        languages={"python": 1},
        key_files=("main.py",),
        file_count=2,
        file_contents={},
    )

    signals = extract_signals("", code)

    assert signals.lockfiles == ()
    assert signals.dependency_available is False
    assert signals.dependency_total == 0
    assert signals.dependency_unpinned == 0


# --------------------------------------------------------------------------- #
# Broadened README-vs-code consistency vocabulary (Group A, task 14.3)
# --------------------------------------------------------------------------- #


def test_group_a_term_matches_only_when_artifact_present() -> None:
    """A Group A term (docker) is a *match* only when its artifact is in the tree.

    The README references "Docker". When the file tree contains a ``Dockerfile``
    the reference is a match (ratio 1.0). Group A availability is governed by the
    README having a detectable reference and the code side being non-empty
    (a language present), so consistency is available in both cases (Req 3.3).
    """
    readme = "Runs in Docker.\n"
    code = StructuredCodeData(
        files=("src/app.py", "Dockerfile"),
        languages={"python": 1},
        key_files=("src/app.py",),
        file_count=2,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.consistency_available is True
    # The single detectable reference ("docker") matches the present Dockerfile.
    assert signals.consistency_ratio == 1.0


def test_group_a_term_is_mismatch_when_artifact_absent() -> None:
    """A Group A term with no corresponding artifact is a mismatch, not vacuous.

    The README references "Kubernetes" but the tree has no k8s/helm artifact, so
    the reference is detectable (availability holds) yet counts as a mismatch —
    ratio 0.0, never a vacuous match (Req 3.4).
    """
    readme = "Deploy on Kubernetes.\n"
    code = StructuredCodeData(
        files=("src/app.py",),
        languages={"python": 1},
        key_files=("src/app.py",),
        file_count=1,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.consistency_available is True
    assert signals.consistency_ratio == 0.0


def test_group_a_term_recognition_is_case_insensitive() -> None:
    """Group A terms are matched case-insensitively against the file tree (Req 3.1)."""
    readme = "Build with MAKE and GraphQL.\n"
    code = StructuredCodeData(
        files=("Makefile", "schema.graphql", "src/app.py"),
        languages={"python": 1},
        key_files=("src/app.py",),
        file_count=3,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.consistency_available is True
    # Both "make" (Makefile) and "graphql" (.graphql) are matches.
    assert signals.consistency_ratio == 1.0


# --------------------------------------------------------------------------- #
# Limited-data consistency semantics (Req 3.6 / 3.7, 2.6): NOT a confident zero
# --------------------------------------------------------------------------- #


def test_no_detectable_references_is_limited_data_not_confident_zero() -> None:
    """README with no detectable references -> consistency_available False (Req 3.6).

    The code side is non-empty (a language + key file), but the README mentions
    no known language, key file, or Group A term, so there is nothing detectable
    to be consistent about. This is limited-data (availability False), which the
    scoring core surfaces as missing_data=True — never a confident zero.
    """
    readme = "A wonderful little project that does delightful things.\n"
    code = StructuredCodeData(
        files=("src/app.py",),
        languages={"python": 1},
        key_files=("src/app.py",),
        file_count=1,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.consistency_available is False
    assert signals.consistency_ratio == 0.0


def test_no_code_languages_or_key_files_is_limited_data() -> None:
    """Code with no languages and no key files -> consistency_available False (Req 3.7).

    Even a README rich in technology references cannot be consistency-scored when
    the code structure exposes neither languages nor key files — limited-data.
    """
    readme = "Written in Python with Docker and Kubernetes.\n"
    code = StructuredCodeData(
        files=("README.md", "notes/todo.md"),
        languages={},
        key_files=(),
        file_count=2,
        file_contents={},
    )

    signals = extract_signals(readme, code)

    assert signals.consistency_available is False
    assert signals.consistency_ratio == 0.0


# --------------------------------------------------------------------------- #
# Regression: the ``mattpocock/skills`` case (task 14.5)
#
# A Markdown/config-heavy repository (many ``.md`` files under ``skills/`` and a
# couple of config files) with NO detected source languages and NO key files.
# Before the extraction-gap fixes, Markdown was excluded from ``files`` so
# Documentation false-zeroed, and consistency emitted a confident 0. After the
# fixes:
#   * documentation files ARE classified -> Documentation is non-zero, and
#   * with no languages/key files, consistency is limited-data (missing_data
#     True) rather than a confident zero (Req 1.3/1.8, 3.7, 2.6).
# --------------------------------------------------------------------------- #

from app.health.models import Category  # noqa: E402
from app.health.scoring import compute_health_score  # noqa: E402


def _mattpocock_skills_code() -> StructuredCodeData:
    """A Markdown/config-heavy repo with no source languages or key files."""
    files = (
        "README.md",
        "skills/typescript/README.md",
        "skills/typescript/guide.md",
        "skills/react/README.md",
        "skills/react/patterns.md",
        "skills/testing/README.md",
        "docs/contributing.md",
        ".github/workflows/ci.yml",
        ".gitignore",
    )
    # No recognized programming-language files -> languages empty; the docs and
    # config carry no key-file basenames -> key_files empty.
    return StructuredCodeData(
        files=files,
        languages={},
        key_files=(),
        file_count=len(files),
        file_contents={},
    )


def test_mattpocock_skills_documentation_is_non_zero() -> None:
    """The Markdown-heavy repo yields detectable documentation, so Documentation
    scores non-zero (regression: Markdown was previously dropped from ``files``).
    """
    readme = (
        "# skills\n\n"
        "A curated set of skills.\n\n"
        "## Usage\n\nBrowse the skills directory.\n"
    )
    code = _mattpocock_skills_code()

    signals = extract_signals(readme, code)

    # Documentation files are now detected (Markdown + docs/ entries).
    assert signals.documentation_available is True
    assert len(signals.doc_files) >= 1

    result = compute_health_score(signals)
    doc = next(c for c in result.categories if c.category is Category.DOCUMENTATION)
    assert doc.missing_data is False
    assert doc.score > 0


def test_mattpocock_skills_consistency_is_limited_data_not_zero() -> None:
    """The repo has no languages and no key files, so Readme_Code_Consistency is
    limited-data (missing_data True, score 0) rather than a confident zero.
    """
    readme = (
        "# skills\n\n"
        "A curated set of skills covering TypeScript and React.\n"
    )
    code = _mattpocock_skills_code()

    signals = extract_signals(readme, code)

    # No languages and no key files -> code side is empty (Req 3.7).
    assert signals.consistency_available is False

    result = compute_health_score(signals)
    consistency = next(
        c for c in result.categories if c.category is Category.README_CODE_CONSISTENCY
    )
    # Limited-data: surfaced as missing_data True, not a confident zero.
    assert consistency.missing_data is True
    assert consistency.score == 0
