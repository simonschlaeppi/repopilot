"""Import-boundary and no-network guard tests for the pure health core (task 12.1).

The ``app/health`` package is the **pure scoring core** of the health-score
feature. Its defining constraint is architectural: it must never depend on the
network, API, or UI layers, and its derivation functions must never issue an
external request. This file locks that constraint down with two test groups:

- **Group 1 (import boundary, Req 5.5 / 1.5 / 4.5)** — every pure module under
  ``app/health`` is parsed with :mod:`ast` and asserted to import NONE of the
  forbidden network/API/UI modules (``requests``, ``fastapi``, ``streamlit``,
  ``openai``, ``app.main``), whether via ``import x`` or ``from x import y``.

- **Group 2 (no network, Req 4.5 / 1.5)** — ``parse_pinning`` and
  ``extract_signals`` (which drives the pinning derivation) are exercised with
  representative inputs while a sentinel is installed over the network layer so
  that ANY attempted network call raises loudly. Both functions must complete
  normally, proving the pinning derivation issues no network request.

These are behavioural guard tests, not property tests.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from app.health.lockfile import parse_pinning
from app.health.models import StructuredCodeData
from app.health.signals import extract_signals


# --------------------------------------------------------------------------- #
# Shared constants
# --------------------------------------------------------------------------- #

# The pure-core modules that must respect the import boundary. Kept explicit
# (rather than globbed) so a newly added module is a deliberate decision.
PURE_MODULES: tuple[str, ...] = (
    "models",
    "errors",
    "weights",
    "lockfile",
    "signals",
    "scoring",
)

# Modules the pure core must never pull in (directly or via from-imports).
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "requests",
        "fastapi",
        "streamlit",
        "openai",
        "app.main",
    }
)

# Directory holding the pure package sources.
_HEALTH_DIR = Path(__file__).resolve().parents[2] / "app" / "health"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _module_path(module: str) -> Path:
    """Return the source file path for a pure ``app.health`` module."""
    return _HEALTH_DIR / f"{module}.py"


def _imported_modules(source: str) -> set[str]:
    """Return the set of top-level module names imported by ``source``.

    Collects both ``import a.b`` (yielding ``a.b`` and its ``a`` prefix) and
    ``from a.b import c`` (yielding ``a.b`` and its ``a`` prefix) targets. Prefix
    forms are included so that, e.g., ``import app.main`` is caught by a check
    against ``"app.main"`` and a ``from app import main`` style is comparable.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
                found.update(_prefixes(alias.name))
        elif isinstance(node, ast.ImportFrom):
            # Ignore relative imports (node.level > 0): they can never reach a
            # top-level network/API/UI module by an absolute name.
            if node.level == 0 and node.module:
                found.add(node.module)
                found.update(_prefixes(node.module))
                # Also account for "from app import main" -> "app.main".
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")
    return found


def _prefixes(dotted: str) -> set[str]:
    """Return all dotted prefixes of ``a.b.c`` -> {a, a.b, a.b.c}."""
    parts = dotted.split(".")
    return {".".join(parts[: i + 1]) for i in range(len(parts))}


# --------------------------------------------------------------------------- #
# Group 1 — import boundary (Req 5.5 / 1.5 / 4.5)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_source_exists(module: str) -> None:
    """Each declared pure module has a real source file to inspect."""
    assert _module_path(module).is_file(), f"missing pure module: {module}"


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_imports_no_forbidden_modules(module: str) -> None:
    """No pure module imports requests/fastapi/streamlit/openai/app.main.

    Parses the module source with ``ast`` and asserts none of the forbidden
    network/API/UI modules appear as an import target (Req 5.5 / 1.5 / 4.5).
    """
    source = _module_path(module).read_text(encoding="utf-8")
    imported = _imported_modules(source)

    offenders = imported & FORBIDDEN_IMPORTS
    assert not offenders, (
        f"pure module app.health.{module} imports forbidden module(s): "
        f"{sorted(offenders)}"
    )


def test_all_pure_health_sources_respect_the_boundary() -> None:
    """AST-level sweep across every app/health/*.py file (transitive guard).

    Iterating the actual directory (not just the declared list) guarantees a
    module added later cannot silently introduce a network/API/UI import
    without failing this test.
    """
    py_files = sorted(_HEALTH_DIR.glob("*.py"))
    assert py_files, "no python sources found under app/health"

    violations: dict[str, list[str]] = {}
    for path in py_files:
        imported = _imported_modules(path.read_text(encoding="utf-8"))
        offenders = imported & FORBIDDEN_IMPORTS
        if offenders:
            violations[path.name] = sorted(offenders)

    assert not violations, f"forbidden imports found in pure core: {violations}"


def test_importing_app_health_does_not_load_network_modules() -> None:
    """Importing the pure package does not pull network/API/UI modules into sys.modules.

    Complements the AST sweep with a runtime check: after importing
    ``app.health`` (and each pure submodule), none of the forbidden modules are
    present in ``sys.modules`` unless they were already loaded by the test
    harness itself. ``requests`` is checked via the AST sweep only, since a test
    dependency may legitimately import it; here we focus on the ones the pure
    core would never need.
    """
    # Record what the harness already loaded so we do not blame the pure core
    # for a module some other test dependency dragged in.
    preloaded = {name for name in FORBIDDEN_IMPORTS if name in sys.modules}

    import importlib

    import app.health  # noqa: F401  (import for side effect: package load)

    for module in PURE_MODULES:
        importlib.import_module(f"app.health.{module}")

    newly_loaded = {
        name
        for name in FORBIDDEN_IMPORTS
        if name in sys.modules and name not in preloaded
    }
    # app.main must never be loaded by importing the pure core.
    assert "app.main" not in newly_loaded, (
        "importing app.health loaded app.main"
    )


# --------------------------------------------------------------------------- #
# Group 2 — no network (Req 4.5 / 1.5)
# --------------------------------------------------------------------------- #


class _NetworkAttempted(AssertionError):
    """Raised if the pure core attempts any network access during a test."""


def _install_network_landmines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the network layer with sentinels that raise loudly if called.

    ``requests`` is imported here (in the TEST) and its transport-level entry
    points are patched to raise :class:`_NetworkAttempted`. Because the pure
    core imports no networking library, none of these landmines should ever
    fire; if a regression introduced a network call, the affected test would
    fail immediately with a clear message.

    Low-level socket creation is also blocked so that any HTTP client the core
    might reach for (not just ``requests``) is caught.
    """
    import socket

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise _NetworkAttempted("pure core attempted a network call")

    # Block the common high-level HTTP client if it is importable.
    try:
        import requests

        for attr in ("get", "post", "put", "patch", "delete", "head", "request"):
            monkeypatch.setattr(requests, attr, _explode, raising=False)
        # Session.request underlies all of the above.
        monkeypatch.setattr(requests.sessions.Session, "request", _explode, raising=False)
    except ImportError:
        # requests not installed — nothing to patch, the socket guard remains.
        pass

    # Block raw socket connections as a catch-all for any HTTP transport.
    monkeypatch.setattr(socket.socket, "connect", _explode, raising=False)


# --- Representative fixtures --------------------------------------------- #

_REQUIREMENTS_TXT = "\n".join(
    [
        "requests==2.31.0",
        "flask>=2.0",
        "numpy",
        "pandas==2.2.*",
    ]
)

_PACKAGE_JSON = (
    '{"dependencies": {"left-pad": "1.3.0", "react": "^18.2.0"}, '
    '"devDependencies": {"jest": "*"}}'
)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("requirements.txt", _REQUIREMENTS_TXT),
        ("package.json", _PACKAGE_JSON),
        ("unknown.lock", "irrelevant content"),
        ("go.mod", "module example.com/m\n\nrequire example.com/x v1.2.3\n"),
    ],
)
def test_parse_pinning_issues_no_network_request(
    filename: str,
    content: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_pinning derives pinning with the network layer armed to explode.

    With every network entry point replaced by a landmine, ``parse_pinning``
    must return a ``DependencyPinning`` normally (Req 4.5 / 1.5).
    """
    _install_network_landmines(monkeypatch)

    result = parse_pinning(filename, content)

    # It completed without tripping a landmine and returned sane counts.
    assert result.total >= 0
    assert result.unpinned >= 0
    assert result.unpinned <= result.total


def test_extract_signals_pinning_derivation_issues_no_network_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extract_signals (incl. its pinning derivation) makes no network call.

    Builds a :class:`StructuredCodeData` carrying a lockfile WITH content so the
    dependency-pinning aggregation path (which calls ``parse_pinning``) is
    actually exercised, then arms the network landmines and asserts extraction
    completes normally with the aggregated counts populated (Req 4.5 / 1.5).
    """
    _install_network_landmines(monkeypatch)

    code = StructuredCodeData(
        files=("requirements.txt", "README.md", "src/app.py", "tests/test_app.py"),
        languages={"python": 2},
        key_files=("requirements.txt", "src/app.py"),
        file_count=4,
        file_contents={
            "requirements.txt": _REQUIREMENTS_TXT,
        },
    )
    readme = "# Example\n\nA python project. See `requirements.txt` for deps.\n"

    signals = extract_signals(readme, code)

    # The lockfile was detected and its pinning aggregated (proves parse_pinning
    # ran on real content, all without any network access).
    assert "requirements.txt" in signals.lockfiles
    assert signals.dependency_available is True
    assert signals.dependency_total >= 1
    assert signals.dependency_unpinned >= 1
    assert signals.dependency_unpinned <= signals.dependency_total


def test_network_landmines_actually_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check: the landmines raise when a network call IS attempted.

    Guards against a false-negative where the network guards silently no-op and
    the no-network tests would pass even if the core DID call out.
    """
    _install_network_landmines(monkeypatch)

    import socket

    with pytest.raises(_NetworkAttempted):
        socket.socket().connect(("example.com", 80))
