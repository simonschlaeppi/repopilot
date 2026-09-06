"""Format-dispatched lockfile pinning derivation for the health score.

This module derives dependency pinning counts directly from lockfile / manifest
contents. It is part of the **pure core**: it imports only the standard library
and ``app.health.models`` and issues **no external network request** (Req 4.5).

The single public entry point is :func:`parse_pinning`. It dispatches on the
lockfile ``filename`` to a format-specific parser and returns a
:class:`~app.health.models.DependencyPinning` counting the total number of
dependency declarations and how many are classified as an ``Unpinned_Dependency``.

Classification rules (Req 4.2 / 4.3):

- **Pinned**: the declaration specifies an exact version (for example ``==1.2.3``
  for pip, an exact npm version such as ``1.2.3``, or a Go pseudo-version).
- **Unpinned_Dependency**: the declaration uses a wildcard, an open or unbounded
  version range, or has no version specifier at all.

Any unknown format or unparseable content yields ``DependencyPinning(0, 0)``
(Req 4 fallback) — this function never raises for content problems.
"""

from __future__ import annotations

import json
import re
import tomllib

from app.health.models import DependencyPinning

__all__ = ["parse_pinning"]


def parse_pinning(filename: str, content: str) -> DependencyPinning:
    """Derive dependency pinning counts from a lockfile's contents.

    Dispatches on ``filename`` (basename) to a format-specific parser. Supported
    formats: ``requirements.txt``, ``package.json``, ``pyproject.toml``,
    ``go.mod`` and ``Cargo.toml``.

    Returns :class:`DependencyPinning` ``(total, unpinned)``. Unknown formats or
    unparseable content return ``DependencyPinning(0, 0)``. Never raises for
    content problems and issues no network request (Req 4.5).
    """
    # Normalize to the basename so full paths like "sub/dir/package.json" work.
    name = filename.rsplit("/", 1)[-1].strip().lower()

    parser = _DISPATCH.get(name)
    if parser is None:
        return DependencyPinning(0, 0)

    try:
        return parser(content)
    except Exception:
        # Unparseable content degrades gracefully to the (0, 0) fallback.
        return DependencyPinning(0, 0)


# --------------------------------------------------------------------------- #
# requirements.txt (pip specifiers)
# --------------------------------------------------------------------------- #

# A pinned pip declaration uses the exact-version operator "==" (and not "===",
# which is arbitrary-equality, still exact) with no wildcard in the version.
_PIP_NAME = r"[A-Za-z0-9._-]+"
_PIP_EXTRAS = r"(?:\[[^\]]*\])?"


def _parse_requirements_txt(content: str) -> DependencyPinning:
    total = 0
    unpinned = 0

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip inline comments (" # ...").
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line:
            continue
        # Skip pip options and includes (-r, -e, --hash, etc.).
        if line.startswith("-"):
            continue
        # Skip URL / VCS / local-path installs (not name==version declarations).
        if "://" in line or line.startswith(("git+", "hg+", "svn+", "bzr+")):
            continue

        name_match = re.match(rf"^{_PIP_NAME}{_PIP_EXTRAS}", line)
        if not name_match:
            continue

        total += 1
        spec = line[name_match.end():].strip()
        # Drop environment markers (";" and beyond) before spec inspection.
        spec = spec.split(";", 1)[0].strip()

        if _pip_is_pinned(spec):
            continue
        unpinned += 1

    return DependencyPinning(total, unpinned)


def _pip_is_pinned(spec: str) -> bool:
    """A pip spec is pinned when it is a single exact "==" (or "===") clause
    to a concrete version with no wildcard."""
    if not spec:
        return False
    # Multiple comma-separated clauses => a range, treat as unpinned.
    clauses = [c.strip() for c in spec.split(",") if c.strip()]
    if len(clauses) != 1:
        return False
    clause = clauses[0]
    match = re.match(r"^(===|==)\s*(.+)$", clause)
    if not match:
        return False
    version = match.group(2).strip()
    # Wildcard versions (e.g. "1.2.*") are unpinned.
    if "*" in version:
        return False
    return bool(version)


# --------------------------------------------------------------------------- #
# package.json (npm dependency maps)
# --------------------------------------------------------------------------- #

_NPM_DEP_KEYS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


def _parse_package_json(content: str) -> DependencyPinning:
    data = json.loads(content)
    if not isinstance(data, dict):
        return DependencyPinning(0, 0)

    total = 0
    unpinned = 0
    for key in _NPM_DEP_KEYS:
        deps = data.get(key)
        if not isinstance(deps, dict):
            continue
        for _dep_name, spec in deps.items():
            total += 1
            if not _npm_is_pinned(spec):
                unpinned += 1

    return DependencyPinning(total, unpinned)


def _npm_is_pinned(spec: object) -> bool:
    """An npm spec is pinned only when it is an exact version like ``1.2.3``.

    ``^``, ``~``, ``>=``/ranges, ``*``, ``latest``, ``x`` wildcards, and
    url/git/file specifiers are all unpinned.
    """
    if not isinstance(spec, str):
        return False
    value = spec.strip()
    if not value:
        return False
    # Anything that is not a bare semver-like exact version is unpinned.
    # Exact: optional leading 'v', digits.digits.digits with optional pre/build.
    return bool(re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", value))


# --------------------------------------------------------------------------- #
# pyproject.toml
# --------------------------------------------------------------------------- #


def _parse_pyproject_toml(content: str) -> DependencyPinning:
    data = tomllib.loads(content)
    total = 0
    unpinned = 0

    project = data.get("project")
    if isinstance(project, dict):
        # PEP 621 dependencies: list of PEP 508 strings.
        deps = project.get("dependencies")
        if isinstance(deps, list):
            for dep in deps:
                if not isinstance(dep, str) or not dep.strip():
                    continue
                total += 1
                if not _pep508_is_pinned(dep):
                    unpinned += 1
        # optional-dependencies: mapping of group -> list of PEP 508 strings.
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if not isinstance(group, list):
                    continue
                for dep in group:
                    if not isinstance(dep, str) or not dep.strip():
                        continue
                    total += 1
                    if not _pep508_is_pinned(dep):
                        unpinned += 1

    # Poetry-style: [tool.poetry.dependencies] mapping name -> spec.
    poetry = data.get("tool", {})
    if isinstance(poetry, dict):
        poetry_cfg = poetry.get("poetry")
        if isinstance(poetry_cfg, dict):
            for key in ("dependencies", "dev-dependencies"):
                deps = poetry_cfg.get(key)
                if not isinstance(deps, dict):
                    continue
                for dep_name, spec in deps.items():
                    if dep_name.lower() == "python":
                        continue  # python constraint, not a dependency
                    total += 1
                    if not _poetry_is_pinned(spec):
                        unpinned += 1

    return DependencyPinning(total, unpinned)


def _pep508_is_pinned(dep: str) -> bool:
    """A PEP 508 requirement string is pinned when its only version clause is an
    exact ``==``/``===`` to a concrete (non-wildcard) version."""
    # Strip extras and environment markers, then reuse the pip logic.
    text = dep.strip()
    text = text.split(";", 1)[0].strip()
    name_match = re.match(rf"^{_PIP_NAME}{_PIP_EXTRAS}", text)
    if not name_match:
        return False
    spec = text[name_match.end():].strip()
    return _pip_is_pinned(spec)


def _poetry_is_pinned(spec: object) -> bool:
    """Poetry deps are pinned when the version is an exact version.

    ``spec`` may be a string (``"1.2.3"``, ``"^1.2"``) or a table
    (``{version = "1.2.3", ...}``). Carets, tildes, wildcards, ranges and
    missing versions are unpinned.
    """
    if isinstance(spec, dict):
        version = spec.get("version")
        return _poetry_version_is_exact(version)
    return _poetry_version_is_exact(spec)


def _poetry_version_is_exact(version: object) -> bool:
    if not isinstance(version, str):
        return False
    value = version.strip()
    if not value:
        return False
    # Exact if it is a bare version (optionally with a leading "==").
    value = value[2:].strip() if value.startswith("==") else value
    if any(ch in value for ch in "^~*<>| "):
        return False
    return bool(re.fullmatch(r"v?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?", value))


# --------------------------------------------------------------------------- #
# go.mod
# --------------------------------------------------------------------------- #

# In go.mod, a require directive lists module + version. Concrete released
# versions (v1.2.3) and pseudo-versions (v0.0.0-20200101000000-abcdef123456)
# are pinned. A bare "latest" or "*" would be unpinned (uncommon in go.mod).
_GO_REQUIRE_LINE = re.compile(
    r"^(?P<module>[^\s]+)\s+(?P<version>[^\s]+)(?:\s+//.*)?$"
)


def _parse_go_mod(content: str) -> DependencyPinning:
    total = 0
    unpinned = 0

    in_block = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        if in_block:
            if line.startswith(")"):
                in_block = False
                continue
            total_delta, unpinned_delta = _go_count_require(line)
            total += total_delta
            unpinned += unpinned_delta
            continue

        if line.startswith("require"):
            rest = line[len("require"):].strip()
            if rest.startswith("("):
                in_block = True
                rest = rest[1:].strip()
                if rest and not rest.startswith(")"):
                    d_total, d_unpinned = _go_count_require(rest)
                    total += d_total
                    unpinned += d_unpinned
                continue
            # Single-line require directive.
            d_total, d_unpinned = _go_count_require(rest)
            total += d_total
            unpinned += d_unpinned

    return DependencyPinning(total, unpinned)


def _go_count_require(line: str) -> tuple[int, int]:
    line = line.strip()
    if not line or line.startswith("//"):
        return 0, 0
    match = _GO_REQUIRE_LINE.match(line)
    if not match:
        return 0, 0
    version = match.group("version")
    return 1, (0 if _go_version_is_pinned(version) else 1)


def _go_version_is_pinned(version: str) -> bool:
    version = version.strip()
    if not version or "*" in version or version == "latest":
        return False
    # Concrete released version or pseudo-version, both start with a semver core.
    return bool(re.match(r"^v\d+\.\d+\.\d+", version))


# --------------------------------------------------------------------------- #
# Cargo.toml
# --------------------------------------------------------------------------- #

_CARGO_DEP_KEYS = ("dependencies", "dev-dependencies", "build-dependencies")


def _parse_cargo_toml(content: str) -> DependencyPinning:
    data = tomllib.loads(content)
    total = 0
    unpinned = 0

    for key in _CARGO_DEP_KEYS:
        deps = data.get(key)
        if not isinstance(deps, dict):
            continue
        for _dep_name, spec in deps.items():
            total += 1
            if not _cargo_is_pinned(spec):
                unpinned += 1

    # target-specific dependencies: [target.'cfg(...)'.dependencies]
    targets = data.get("target")
    if isinstance(targets, dict):
        for target_cfg in targets.values():
            if not isinstance(target_cfg, dict):
                continue
            for key in _CARGO_DEP_KEYS:
                deps = target_cfg.get(key)
                if not isinstance(deps, dict):
                    continue
                for _dep_name, spec in deps.items():
                    total += 1
                    if not _cargo_is_pinned(spec):
                        unpinned += 1

    return DependencyPinning(total, unpinned)


def _cargo_is_pinned(spec: object) -> bool:
    """Cargo deps are pinned when an exact version (``=1.2.3``) is required.

    A bare ``"1.2.3"`` in Cargo is a caret range (``^1.2.3``), i.e. unpinned.
    Only an explicit ``=`` requirement pins the version. ``spec`` may be a
    string or a table (``{version = "=1.2.3", ...}``).
    """
    if isinstance(spec, dict):
        version = spec.get("version")
        if version is None:
            # git/path dependency with no version requirement => unpinned.
            return False
        return _cargo_version_is_exact(version)
    return _cargo_version_is_exact(spec)


def _cargo_version_is_exact(version: object) -> bool:
    if not isinstance(version, str):
        return False
    value = version.strip()
    if not value.startswith("="):
        # Bare / caret / tilde / wildcard / range are all unpinned in Cargo.
        return False
    core = value[1:].strip()
    if not core or "*" in core:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?", core))


# Dispatch table maps a lowercase basename to its format parser.
_DISPATCH = {
    "requirements.txt": _parse_requirements_txt,
    "package.json": _parse_package_json,
    "pyproject.toml": _parse_pyproject_toml,
    "go.mod": _parse_go_mod,
    "cargo.toml": _parse_cargo_toml,
}
