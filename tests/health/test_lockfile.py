"""Unit tests for app.health.lockfile.parse_pinning edge cases (task 4.4).

Covers:

- Unknown filename -> DependencyPinning(0, 0) (Req 4 fallback).
- Empty / malformed content (bad JSON, bad TOML) -> DependencyPinning(0, 0).
- Per-format fixtures asserting exact total / unpinned counts for:
  - requirements.txt (pip specifiers)
  - package.json (npm dependency maps)
  - pyproject.toml (PEP 621 and Poetry)
  - go.mod (pseudo-version pinned + released version)
  - Cargo.toml (bare caret range = unpinned; =1.2.3 = pinned)

Classification rules under test (Req 4.2 / 4.3):
- exact version -> pinned
- wildcard / open range / no specifier -> Unpinned_Dependency
"""

from __future__ import annotations

import pytest

from app.health.lockfile import parse_pinning
from app.health.models import DependencyPinning


# --------------------------------------------------------------------------- #
# Unknown filename fallback (Req 4 fallback)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "filename",
    [
        "unknown.txt",
        "Gemfile",
        "yarn.lock",
        "poetry.lock",
        "README.md",
        "",
    ],
)
def test_unknown_filename_returns_zero_zero(filename):
    """An unrecognized filename yields DependencyPinning(0, 0)."""
    result = parse_pinning(filename, "anything at all\nname==1.2.3\n")
    assert result == DependencyPinning(0, 0)


# --------------------------------------------------------------------------- #
# Empty and malformed content -> (0, 0) (never raises)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "filename",
    [
        "requirements.txt",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
    ],
)
def test_empty_content_returns_zero_zero(filename):
    """Empty content for a known format yields DependencyPinning(0, 0)."""
    assert parse_pinning(filename, "") == DependencyPinning(0, 0)
    assert parse_pinning(filename, "   \n\n  \n") == DependencyPinning(0, 0)


def test_malformed_package_json_returns_zero_zero():
    """Malformed JSON degrades gracefully to (0, 0) rather than raising."""
    assert parse_pinning("package.json", "{ not: valid json,,,") == DependencyPinning(
        0, 0
    )
    # Valid JSON but not an object (e.g. a JSON array) -> (0, 0).
    assert parse_pinning("package.json", "[1, 2, 3]") == DependencyPinning(0, 0)


def test_malformed_pyproject_toml_returns_zero_zero():
    """Malformed TOML degrades gracefully to (0, 0)."""
    assert parse_pinning(
        "pyproject.toml", "this is = = not [ valid toml"
    ) == DependencyPinning(0, 0)


def test_malformed_cargo_toml_returns_zero_zero():
    """Malformed TOML for Cargo degrades gracefully to (0, 0)."""
    assert parse_pinning(
        "Cargo.toml", "[dependencies\nserde = "
    ) == DependencyPinning(0, 0)


# --------------------------------------------------------------------------- #
# requirements.txt (pip specifiers)
# --------------------------------------------------------------------------- #


def test_requirements_txt_mix_of_pinned_and_unpinned():
    """A requirements.txt with a mix of pinned and unpinned declarations.

    Pinned: exact "==" clause. Unpinned: open range, wildcard, no specifier.
    """
    content = "\n".join(
        [
            "# a comment line, ignored",
            "",
            "requests==2.31.0",  # pinned
            "flask==3.0.*",  # unpinned (wildcard)
            "django>=4.0",  # unpinned (open range)
            "numpy",  # unpinned (no specifier)
            "pytest==7.4.4  # inline comment",  # pinned (comment stripped)
            "-r other-requirements.txt",  # option line, skipped
            "urllib3>=1.26,<2.0",  # unpinned (multi-clause range)
        ]
    )

    result = parse_pinning("requirements.txt", content)

    # requests, flask, django, numpy, pytest, urllib3 == 6 declarations.
    assert result.total == 6
    # flask, django, numpy, urllib3 == 4 unpinned.
    assert result.unpinned == 4


def test_requirements_txt_all_pinned():
    """All exact "==" declarations count as pinned (zero unpinned)."""
    content = "alpha==1.0.0\nbeta==2.3.4\ngamma===9.9.9\n"
    result = parse_pinning("requirements.txt", content)
    assert result == DependencyPinning(3, 0)


# --------------------------------------------------------------------------- #
# package.json (npm dependency maps)
# --------------------------------------------------------------------------- #


def test_package_json_dependencies_and_dev_dependencies():
    """dependencies + devDependencies maps mixing exact and ^/~/range specs."""
    content = """
    {
      "name": "demo",
      "version": "1.0.0",
      "dependencies": {
        "left-pad": "1.3.0",
        "react": "^18.2.0",
        "lodash": "~4.17.21"
      },
      "devDependencies": {
        "typescript": "5.3.3",
        "eslint": ">=8.0.0",
        "jest": "*"
      }
    }
    """

    result = parse_pinning("package.json", content)

    # 3 deps + 3 devDeps == 6 declarations.
    assert result.total == 6
    # Pinned: left-pad (1.3.0), typescript (5.3.3). Unpinned: react (^),
    # lodash (~), eslint (>=), jest (*) == 4 unpinned.
    assert result.unpinned == 4


def test_package_json_no_dependency_maps():
    """A package.json with no dependency maps yields (0, 0)."""
    content = '{"name": "demo", "version": "1.0.0"}'
    assert parse_pinning("package.json", content) == DependencyPinning(0, 0)


# --------------------------------------------------------------------------- #
# pyproject.toml (PEP 621 and Poetry)
# --------------------------------------------------------------------------- #


def test_pyproject_toml_pep621_dependencies():
    """PEP 621 [project.dependencies] mixing exact and open specs."""
    content = "\n".join(
        [
            "[project]",
            'name = "demo"',
            'version = "0.1.0"',
            "dependencies = [",
            '    "requests==2.31.0",',  # pinned
            '    "click>=8.0",',  # unpinned (open range)
            '    "rich",',  # unpinned (no specifier)
            "]",
            "",
            "[project.optional-dependencies]",
            'test = ["pytest==7.4.4", "coverage"]',  # pinned + unpinned
        ]
    )

    result = parse_pinning("pyproject.toml", content)

    # requests, click, rich, pytest, coverage == 5 declarations.
    assert result.total == 5
    # click, rich, coverage == 3 unpinned.
    assert result.unpinned == 3


def test_pyproject_toml_poetry_dependencies():
    """Poetry [tool.poetry.dependencies] mapping; python constraint is skipped."""
    content = "\n".join(
        [
            "[tool.poetry.dependencies]",
            'python = "^3.11"',  # skipped (python constraint, not a dep)
            'requests = "2.31.0"',  # pinned (bare exact)
            'flask = "^3.0"',  # unpinned (caret)
            'django = "~4.2"',  # unpinned (tilde)
            "",
            "[tool.poetry.dev-dependencies]",
            'pytest = "7.4.4"',  # pinned
        ]
    )

    result = parse_pinning("pyproject.toml", content)

    # requests, flask, django, pytest == 4 declarations (python excluded).
    assert result.total == 4
    # flask, django == 2 unpinned.
    assert result.unpinned == 2


# --------------------------------------------------------------------------- #
# go.mod (pseudo-version pinned + released version)
# --------------------------------------------------------------------------- #


def test_go_mod_pseudo_version_and_released_version_are_pinned():
    """go.mod require block: released version and pseudo-version both pinned."""
    content = "\n".join(
        [
            "module example.com/demo",
            "",
            "go 1.21",
            "",
            "require (",
            "    github.com/pkg/errors v0.9.1",  # pinned (released)
            "    golang.org/x/sync v0.0.0-20200625203802-6e8e738ad208",  # pinned (pseudo)
            ")",
        ]
    )

    result = parse_pinning("go.mod", content)

    assert result.total == 2
    assert result.unpinned == 0


def test_go_mod_single_line_require():
    """A single-line require directive is counted."""
    content = "module example.com/demo\n\nrequire github.com/pkg/errors v0.9.1\n"
    result = parse_pinning("go.mod", content)
    assert result == DependencyPinning(1, 0)


# --------------------------------------------------------------------------- #
# Cargo.toml (bare = caret range = unpinned; =1.2.3 = pinned)
# --------------------------------------------------------------------------- #


def test_cargo_toml_bare_is_unpinned_and_exact_is_pinned():
    """Cargo: bare version is an implicit caret range (unpinned); =x.y.z pins."""
    content = "\n".join(
        [
            "[package]",
            'name = "demo"',
            'version = "0.1.0"',
            "",
            "[dependencies]",
            'serde = "1.0.188"',  # unpinned (bare == implicit caret)
            'rand = "=0.8.5"',  # pinned (explicit exact)
            'tokio = { version = "=1.35.0", features = ["full"] }',  # pinned (table)
            'anyhow = { version = "1.0", features = ["backtrace"] }',  # unpinned (table caret)
            "",
            "[dev-dependencies]",
            'proptest = "1.4.0"',  # unpinned (bare)
        ]
    )

    result = parse_pinning("Cargo.toml", content)

    # serde, rand, tokio, anyhow, proptest == 5 declarations.
    assert result.total == 5
    # serde, anyhow, proptest == 3 unpinned; rand + tokio pinned.
    assert result.unpinned == 3
