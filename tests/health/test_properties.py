"""Property-based tests for the repo-health-score pure core.

One test per correctness property, minimum 100 iterations, each tagged with the
canonical ``# Feature: repo-health-score, Property {n}: {text}`` comment.
"""

from __future__ import annotations

import dataclasses
import json
import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.health.errors import WeightConfigError
from app.health.lockfile import parse_pinning
from app.health.models import Category
from app.health.weights import normalize_weights

# --------------------------------------------------------------------------- #
# Shared generators
# --------------------------------------------------------------------------- #

# A pip / npm package name is a lowercase-ish identifier. We generate valid,
# distinct names so declaration counts are unambiguous per format.
_NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _names(min_size: int, max_size: int) -> st.SearchStrategy[list[str]]:
    """A list of unique, format-valid dependency names."""
    name = st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=12).filter(
        # Must start with an alphanumeric (valid for both pip and npm).
        lambda s: s[0].isalnum()
    )
    return st.lists(name, min_size=min_size, max_size=max_size, unique=True)


# A version spec: a mix of pinned (exact) and unpinned (wildcard / range / open)
# forms. The exact string does not affect the *count*, only the mix requested by
# the task ("mix of pinned/unpinned").
_VERSION_SPECS = st.sampled_from(
    [
        "==1.2.3",  # pinned (pip)
        "==2.0.0",  # pinned (pip)
        ">=1.0",  # unpinned range
        "*",  # wildcard
        "",  # no specifier (unpinned)
        "~=1.4",  # compatible-release (unpinned)
    ]
)

_NPM_SPECS = st.sampled_from(
    [
        "1.2.3",  # pinned (npm exact)
        "2.0.0",  # pinned (npm exact)
        "^1.0.0",  # caret range (unpinned)
        "~1.2.0",  # tilde range (unpinned)
        ">=1.0.0",  # open range (unpinned)
        "*",  # wildcard (unpinned)
        "latest",  # tag (unpinned)
    ]
)


# --------------------------------------------------------------------------- #
# Property 16: Lockfile parse preserves declaration count
# --------------------------------------------------------------------------- #


@st.composite
def _requirements_txt(draw) -> tuple[str, int]:
    """Render a requirements.txt from a list of declarations.

    Returns the rendered content and the exact number of declarations rendered.
    """
    names = draw(_names(0, 15))
    lines: list[str] = []
    for name in names:
        spec = draw(_VERSION_SPECS)
        lines.append(f"{name}{spec}")

    # Interleave comment and blank lines, which must NOT be counted as
    # declarations. Their presence exercises the parser's line filtering while
    # leaving the declaration count equal to len(names).
    decorated: list[str] = []
    for line in lines:
        if draw(st.booleans()):
            decorated.append("# a comment")
        decorated.append(line)
        if draw(st.booleans()):
            decorated.append("")  # blank line
    if draw(st.booleans()):
        decorated.append("# trailing comment")

    return "\n".join(decorated), len(names)


@st.composite
def _package_json(draw) -> tuple[str, int]:
    """Render a package.json from dependency declarations.

    Declarations are spread across the recognized npm dependency maps
    (dependencies / devDependencies). Names are unique per map so every key is a
    distinct declaration and the total count is unambiguous.
    """
    dep_names = draw(_names(0, 10))
    dev_names = draw(_names(0, 10))

    dependencies = {name: draw(_NPM_SPECS) for name in dep_names}
    dev_dependencies = {name: draw(_NPM_SPECS) for name in dev_names}

    obj: dict[str, object] = {"name": "generated-pkg", "version": "0.0.0"}
    if dependencies or draw(st.booleans()):
        obj["dependencies"] = dependencies
    if dev_dependencies or draw(st.booleans()):
        obj["devDependencies"] = dev_dependencies

    total = len(dependencies) + len(dev_dependencies)
    return json.dumps(obj, indent=2), total


@st.composite
def _lockfile_case(draw) -> tuple[str, str, int]:
    """Choose a supported format and render it. Returns (filename, content, count)."""
    fmt = draw(st.sampled_from(["requirements.txt", "package.json"]))
    if fmt == "requirements.txt":
        content, count = draw(_requirements_txt())
    else:
        content, count = draw(_package_json())
    return fmt, content, count


# Feature: repo-health-score, Property 16: Lockfile parse preserves declaration count
@given(case=_lockfile_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_16_parse_preserves_declaration_count(case):
    """For any set of dependency declarations rendered into a supported lockfile
    format, parsing yields a total declaration count equal to the number
    rendered.

    **Validates: Requirements 4.1**
    """
    filename, content, expected_count = case
    result = parse_pinning(filename, content)
    assert result.total == expected_count


# --------------------------------------------------------------------------- #
# Property 25: Invalid weight configuration raises a configuration error
# --------------------------------------------------------------------------- #

# All five category members, used to build fully-populated weight configs so
# the "missing category" branch (Property 24 / Req 6.5) never fires and we
# isolate the invalid-value conditions of Req 6.6.
_CATEGORIES = tuple(Category)

# A finite, non-negative numeric weight used to pad the "valid" slots of a
# configuration so only the injected invalid condition can trigger the error.
_valid_weight = st.floats(
    min_value=0.0,
    max_value=1_000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Strictly-negative weights (Req 6.6: negative weight condition).
_negative_weight = st.floats(
    min_value=-1_000.0,
    max_value=-1e-6,
    allow_nan=False,
    allow_infinity=False,
)

# Non-numeric weights (Req 6.6: non-numeric condition). ``bool`` is included
# because ``normalize_weights`` treats booleans as non-numeric even though
# ``bool`` subclasses ``int``.
_non_numeric_weight = st.one_of(
    st.text(),
    st.none(),
    st.booleans(),
    st.lists(st.integers()),
    st.tuples(st.integers()),
)


@st.composite
def _invalid_weight_config(draw) -> tuple[dict, str]:
    """Generate a fully-populated weight config that is invalid for exactly one
    of the three Req 6.6 reasons: a negative weight, a non-numeric weight, or
    five weights summing to zero.
    """
    condition = draw(st.sampled_from(("negative", "non_numeric", "zero_sum")))

    if condition == "zero_sum":
        # All five weights are zero -> the set sums to zero (Req 6.6).
        return {category: 0.0 for category in _CATEGORIES}, condition

    # Start from a valid, fully-populated config, then corrupt exactly one slot.
    config = {category: draw(_valid_weight) for category in _CATEGORIES}
    target = draw(st.sampled_from(_CATEGORIES))
    if condition == "negative":
        config[target] = draw(_negative_weight)
    else:  # non_numeric
        config[target] = draw(_non_numeric_weight)
    return config, condition


# Feature: repo-health-score, Property 25: Invalid weight configuration raises a configuration error
@given(case=_invalid_weight_config())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_25_invalid_weight_config_raises_config_error(case):
    """For any weight config (all five categories present) containing a negative
    weight, a non-numeric weight, or five weights summing to zero,
    ``normalize_weights`` raises ``WeightConfigError`` and produces no result.

    NOTE: The design phrases Property 25 against ``compute_health_score``, which
    is not yet implemented (task 7.1). ``compute_health_score`` normalizes the
    weights at compute time, delegating this exact validation to
    ``normalize_weights``; when scoring lands it inherits this guarantee.

    **Validates: Requirements 6.6**
    """
    config, condition = case

    # Sanity-check the generator: the config is fully populated (so the missing
    # -category branch cannot mask the intended condition) and truly exhibits
    # the claimed invalid condition.
    assert set(config) == set(_CATEGORIES)
    if condition == "negative":
        assert any(
            isinstance(w, (int, float)) and not isinstance(w, bool) and w < 0
            for w in config.values()
        )
    elif condition == "non_numeric":
        assert any(
            isinstance(w, bool) or not isinstance(w, (int, float))
            for w in config.values()
        )
    else:  # zero_sum
        assert all(
            isinstance(w, (int, float)) and not isinstance(w, bool)
            for w in config.values()
        )
        assert math.fsum(float(w) for w in config.values()) == 0.0

    try:
        normalize_weights(config)
    except WeightConfigError:
        return  # expected: no composite is produced
    raise AssertionError(
        f"normalize_weights accepted an invalid ({condition}) config: {config!r}"
    )

# --------------------------------------------------------------------------- #
# Property 23: Composite is invariant under positive weight scaling
# --------------------------------------------------------------------------- #

# A finite, non-negative weight used to build a valid, fully-populated config.
# Kept comfortably away from float overflow so scaling stays finite.
_scalable_weight = st.floats(
    min_value=0.0,
    max_value=1_000.0,
    allow_nan=False,
    allow_infinity=False,
)

# A strictly-positive, finite scale factor. Bounded well inside the finite
# range so ``weight * scale`` never overflows to infinity for the weights above.
_positive_scale = st.floats(
    min_value=1e-6,
    max_value=1e6,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _valid_weight_config_and_scale(draw) -> tuple[dict, float]:
    """Generate a valid, fully-populated weight config (all five categories,
    non-negative, finite, positive sum) together with a positive finite scale.

    The sum is forced strictly positive by ensuring at least one category has a
    weight bounded away from zero, so both ``normalize_weights(weights)`` and
    ``normalize_weights(scaled)`` are well-defined (never the zero-sum error).
    """
    config = {category: draw(_scalable_weight) for category in _CATEGORIES}
    # Guarantee a positive sum: pin one category to a strictly-positive weight.
    anchor = draw(st.sampled_from(_CATEGORIES))
    config[anchor] = draw(
        st.floats(
            min_value=1e-3,
            max_value=1_000.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    scale = draw(_positive_scale)
    return config, scale


# Feature: repo-health-score, Property 23: Composite is invariant under positive weight scaling
@given(case=_valid_weight_config_and_scale())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_23_composite_invariant_under_positive_weight_scaling(case):
    """For any valid weight config (all five categories present, non-negative,
    finite, positive sum) and any positive finite scale factor,
    ``normalize_weights(scaled_weights)`` equals ``normalize_weights(weights)``
    per category within float tolerance.

    NOTE: The design phrases Property 23 against ``compute_health_score`` (task
    7.1, not yet implemented). Composite invariance follows because scoring
    normalizes the weights at compute time: multiplying every raw weight by a
    positive constant leaves the normalized weights - and therefore the weighted
    composite - unchanged. Verifying the invariance directly on
    ``normalize_weights`` establishes the guarantee scoring will inherit.

    **Validates: Requirements 6.4**
    """
    weights, scale = case

    scaled = {category: weights[category] * scale for category in _CATEGORIES}

    baseline = normalize_weights(weights)
    rescaled = normalize_weights(scaled)

    # Both normalizations cover exactly the five categories.
    assert set(baseline) == set(_CATEGORIES)
    assert set(rescaled) == set(_CATEGORIES)

    for category in _CATEGORIES:
        assert math.isclose(
            baseline[category],
            rescaled[category],
            rel_tol=1e-9,
            abs_tol=1e-12,
        ), (
            f"scaling by {scale!r} changed the normalized weight for "
            f"{category.value!r}: {baseline[category]!r} vs {rescaled[category]!r}"
        )

# --------------------------------------------------------------------------- #
# Property 24: Missing weight raises a named configuration error
# --------------------------------------------------------------------------- #

# A finite, non-negative numeric weight for the categories that ARE present, so
# the ONLY invalid condition in the generated config is the single omission and
# nothing else (negative/non-numeric/zero-sum) can mask or pre-empt it.
_present_weight = st.floats(
    min_value=0.0,
    max_value=1_000.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _config_missing_one_category(draw) -> tuple[dict, Category]:
    """Generate a weight config that omits EXACTLY one of the five categories.

    The other four categories are present with finite, non-negative weights, so
    the missing-category condition (Req 6.5) is the sole reason the config is
    invalid. Returns the config and the single omitted category.
    """
    missing = draw(st.sampled_from(_CATEGORIES))
    config = {
        category: draw(_present_weight)
        for category in _CATEGORIES
        if category is not missing
    }
    return config, missing


# Feature: repo-health-score, Property 24: Missing weight raises a named configuration error
@given(case=_config_missing_one_category())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_24_missing_weight_raises_named_config_error(case):
    """For any weight config that omits exactly one of the five categories (the
    other four present, non-negative, finite), ``normalize_weights`` raises
    ``WeightConfigError`` whose message names the missing category (its ``.value``
    string).

    NOTE: The design phrases Property 24 against ``compute_health_score`` (task
    7.1, not yet implemented). ``compute_health_score`` normalizes the weights at
    compute time, delegating this validation to ``normalize_weights``; when
    scoring lands it inherits this named-error guarantee.

    **Validates: Requirements 6.5**
    """
    config, missing = case

    # Sanity-check the generator: exactly one category is omitted and every
    # present weight is finite and non-negative (so only the omission is wrong).
    assert set(config) == set(_CATEGORIES) - {missing}
    assert all(
        isinstance(w, float) and math.isfinite(w) and w >= 0.0
        for w in config.values()
    )

    try:
        normalize_weights(config)
    except WeightConfigError as exc:
        assert missing.value in str(exc), (
            f"error message did not name the missing category {missing.value!r}: "
            f"{exc!s}"
        )
        return
    raise AssertionError(
        f"normalize_weights accepted a config missing {missing.value!r}: {config!r}"
    )

# --------------------------------------------------------------------------- #
# Property 17: Pinned/unpinned classification is correct
# --------------------------------------------------------------------------- #

# Each spec is paired with its KNOWN classification label so the test controls
# the pinned/unpinned split exactly, rather than trusting the parser to agree
# with itself. ``True`` == unpinned (an Unpinned_Dependency), ``False`` ==
# pinned (an exact version).
#
# pip (requirements.txt) rules (Req 4.2/4.3):
#   pinned   -> a single exact "==x.y.z" (or "===") clause, no wildcard
#   unpinned -> wildcard, open/unbounded range, compatible-release, or no
#               specifier at all.
_PIP_LABELLED_SPECS = st.sampled_from(
    [
        ("==1.2.3", False),  # exact -> pinned
        ("==2.0.0", False),  # exact -> pinned
        ("===0.9.1", False),  # arbitrary-equality exact -> pinned
        (">=1.0", True),  # open range -> unpinned
        (">=1.0,<2.0", True),  # bounded range (2 clauses) -> unpinned
        ("<=3.4", True),  # open upper bound -> unpinned
        ("~=1.4", True),  # compatible-release -> unpinned
        ("==1.2.*", True),  # wildcard exact -> unpinned
        ("*", True),  # bare wildcard -> unpinned
        ("", True),  # no specifier at all -> unpinned
    ]
)

# npm (package.json) rules (Req 4.2/4.3):
#   pinned   -> a bare exact semver "x.y.z" (optionally "vx.y.z")
#   unpinned -> caret/tilde ranges, open ranges, wildcards, dist-tags.
_NPM_LABELLED_SPECS = st.sampled_from(
    [
        ("1.2.3", False),  # exact -> pinned
        ("2.0.0", False),  # exact -> pinned
        ("v3.1.4", False),  # exact with leading v -> pinned
        ("^1.0.0", True),  # caret range -> unpinned
        ("~1.2.0", True),  # tilde range -> unpinned
        (">=1.0.0", True),  # open range -> unpinned
        ("1.x", True),  # wildcard -> unpinned
        ("*", True),  # wildcard -> unpinned
        ("latest", True),  # dist-tag -> unpinned
        ("", True),  # empty -> unpinned
    ]
)


@st.composite
def _labelled_requirements_txt(draw) -> tuple[str, int, int]:
    """Render a requirements.txt where every declaration carries a known label.

    Returns ``(content, total, expected_unpinned)`` where ``total`` is the number
    of declarations rendered and ``expected_unpinned`` is how many of them were
    labelled unpinned.
    """
    names = draw(_names(0, 15))
    lines: list[str] = []
    expected_unpinned = 0
    for name in names:
        spec, is_unpinned = draw(_PIP_LABELLED_SPECS)
        expected_unpinned += int(is_unpinned)
        lines.append(f"{name}{spec}")

    # Interleave comments/blank lines that must not be counted or classified.
    decorated: list[str] = []
    for line in lines:
        if draw(st.booleans()):
            decorated.append("# comment")
        decorated.append(line)
        if draw(st.booleans()):
            decorated.append("")
    return "\n".join(decorated), len(names), expected_unpinned


@st.composite
def _labelled_package_json(draw) -> tuple[str, int, int]:
    """Render a package.json where every dependency carries a known label.

    Declarations are spread across ``dependencies``/``devDependencies`` with
    unique names per map. Returns ``(content, total, expected_unpinned)``.
    """
    dep_names = draw(_names(0, 10))
    dev_names = draw(_names(0, 10))

    expected_unpinned = 0

    def _build(names: list[str]) -> dict[str, str]:
        nonlocal expected_unpinned
        result: dict[str, str] = {}
        for name in names:
            spec, is_unpinned = draw(_NPM_LABELLED_SPECS)
            expected_unpinned += int(is_unpinned)
            result[name] = spec
        return result

    dependencies = _build(dep_names)
    dev_dependencies = _build(dev_names)

    obj: dict[str, object] = {"name": "generated-pkg", "version": "0.0.0"}
    if dependencies or draw(st.booleans()):
        obj["dependencies"] = dependencies
    if dev_dependencies or draw(st.booleans()):
        obj["devDependencies"] = dev_dependencies

    total = len(dependencies) + len(dev_dependencies)
    return json.dumps(obj, indent=2), total, expected_unpinned


@st.composite
def _labelled_lockfile_case(draw) -> tuple[str, str, int, int]:
    """Choose a supported format and render a labelled case.

    Returns ``(filename, content, total, expected_unpinned)``.
    """
    fmt = draw(st.sampled_from(["requirements.txt", "package.json"]))
    if fmt == "requirements.txt":
        content, total, expected_unpinned = draw(_labelled_requirements_txt())
    else:
        content, total, expected_unpinned = draw(_labelled_package_json())
    return fmt, content, total, expected_unpinned


# Feature: repo-health-score, Property 17: Pinned/unpinned classification is correct
@given(case=_labelled_lockfile_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_17_pinned_unpinned_classification_is_correct(case):
    """For any lockfile whose declarations are generated with a known
    pinned/unpinned split, the parser classifies exactly the exact-version
    declarations as pinned and exactly the wildcard, open/unbounded-range, and
    no-specifier declarations as Unpinned_Dependency.

    **Validates: Requirements 4.2, 4.3**
    """
    filename, content, total, expected_unpinned = case
    result = parse_pinning(filename, content)
    # The total must match (guards against mis-counted declarations) ...
    assert result.total == total
    # ... and the unpinned count must equal the number labelled unpinned, which
    # implies the pinned count (total - unpinned) is also exact.
    assert result.unpinned == expected_unpinned

# --------------------------------------------------------------------------- #
# Property 9: Availability flags match data presence
# --------------------------------------------------------------------------- #

import re  # noqa: E402

from app.health.models import StructuredCodeData  # noqa: E402
from app.health.signals import (  # noqa: E402
    KNOWN_LANGUAGES,
    extract_signals,
)

# README bodies: sometimes empty / whitespace-only (absent after strip),
# sometimes genuinely non-empty. The exact text does not matter for the
# availability IFF relationships this property checks — only presence does.
_README_TEXT = st.one_of(
    st.just(""),
    st.text(alphabet=" \t\n\r", min_size=0, max_size=8),  # whitespace-only -> absent
    st.text(min_size=1, max_size=200).filter(lambda s: bool(s.strip())),  # present
)

# --- File-path pools, each pinned to exactly one classification group so the #
# generator controls, per path, whether it is a doc / test / ci / lockfile /  #
# "other" file. These mirror the classifiers in app.health.signals.           #
_DOC_PATHS = st.sampled_from(
    ["README_extra.md", "guide.markdown", "manual.rst", "docs/intro.txt", "doc/api.html"]
)
_TEST_PATHS = st.sampled_from(
    ["test_core.py", "core_test.py", "tests/test_it.py", "app.spec.ts", "foo.test.js"]
)
_CI_PATHS = st.sampled_from(
    [".github/workflows/ci.yml", ".circleci/config.yml", ".gitlab-ci.yml", ".travis.yml", "Jenkinsfile"]
)
_LOCKFILE_PATHS = st.sampled_from(
    ["requirements.txt", "package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml"]
)
# "Other" files: deliberately NOT matching any classification group above.
_OTHER_PATHS = st.sampled_from(
    ["src/main.py", "lib/util.js", "index.html", "assets/logo.png", "Makefile", "app/core.go"]
)


@st.composite
def _structured_code_data(draw) -> StructuredCodeData:
    """Generate a StructuredCodeData with an independently-controlled mix of
    documentation / test / CI / lockfile / other file paths, a languages map, a
    key_files tuple, and a file_contents dict.

    Each group is drawn independently (possibly empty) so the resulting signals
    exercise every combination of present/absent doc, test, ci, and lockfile
    groups — which is exactly what the availability IFF relationships depend on.
    """
    docs = draw(st.lists(_DOC_PATHS, min_size=0, max_size=3, unique=True))
    tests = draw(st.lists(_TEST_PATHS, min_size=0, max_size=3, unique=True))
    cis = draw(st.lists(_CI_PATHS, min_size=0, max_size=3, unique=True))
    locks = draw(st.lists(_LOCKFILE_PATHS, min_size=0, max_size=3, unique=True))
    others = draw(st.lists(_OTHER_PATHS, min_size=0, max_size=3, unique=True))

    files = tuple(docs + tests + cis + locks + others)

    languages = draw(
        st.dictionaries(
            keys=st.sampled_from(sorted(KNOWN_LANGUAGES)),
            values=st.integers(min_value=1, max_value=50),
            max_size=5,
        )
    )
    key_files = tuple(
        draw(
            st.lists(
                st.sampled_from(
                    ["package.json", "go.mod", "Cargo.toml", "pyproject.toml", "setup.py"]
                ),
                max_size=4,
                unique=True,
            )
        )
    )
    # file_contents: keyed by (a subset of) the generated paths. Lockfile
    # contents are irrelevant to availability, so trivial strings suffice.
    contents_keys = draw(
        st.lists(st.sampled_from(files), max_size=len(files), unique=True)
    ) if files else []
    file_contents = {path: "x" for path in contents_keys}

    return StructuredCodeData(
        files=files,
        languages=languages,
        key_files=key_files,
        file_count=len(files),
        file_contents=file_contents,
    )


# code is sometimes absent (None), sometimes a fully-populated structured object.
_CODE_OR_NONE = st.one_of(st.none(), _structured_code_data())


def _has_doc(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    lower = path.lower()
    return name.endswith((".md", ".markdown", ".rst")) or "docs/" in lower or "doc/" in lower


def _has_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    lower = path.lower()
    if any(f"/{seg}/" in f"/{lower}/" for seg in ("test", "tests", "spec", "specs", "__tests__")):
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return (
        stem.startswith(("test_", "test-"))
        or stem.endswith(("_test", "-test", ".test", ".spec"))
        or ".test." in name
        or ".spec." in name
    )


# Feature: repo-health-score, Property 9: Availability flags match data presence
@given(readme=_README_TEXT, code=_CODE_OR_NONE)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_9_availability_flags_match_data_presence(readme, code):
    """For any README text and StructuredCodeData inputs, each RepositorySignals
    availability flag is true if and only if its corresponding source data is
    present; when a required input is absent, the affected fields hold empty
    collections or empty strings.

    **Validates: Requirements 1.6, 1.7**
    """
    signals = extract_signals(readme, code)

    # --- README availability (Req 1.6/1.7) --- #
    readme_present = bool((readme or "").strip())
    assert signals.readme_available == readme_present
    if not readme_present:
        # Whitespace-only/empty README is "absent": readme_text is preserved as
        # given (empty string when None), and the flag is false.
        assert signals.readme_available is False
    if readme is None:
        assert signals.readme_text == ""

    # --- Code-derived collections when code is absent (Req 1.6) --- #
    if code is None:
        assert signals.doc_files == ()
        assert signals.test_files == ()
        assert signals.ci_files == ()
        assert signals.lockfiles == ()
        assert signals.key_files == ()
        assert signals.languages == {}
        # Every code-derived availability flag is false when there is no code.
        assert signals.documentation_available is False
        assert signals.test_coverage_available is False
        assert signals.dependency_available is False
        return

    # --- Code present: each flag is the IFF of its source data presence --- #
    has_docs = any(_has_doc(p) for p in code.files)
    has_tests = any(_has_test(p) for p in code.files)
    has_ci = any(
        ".github/workflows/" in p.lower()
        or ".circleci" in p.lower().split("/")
        or p.rsplit("/", 1)[-1].lower()
        in {
            ".gitlab-ci.yml",
            ".travis.yml",
            "azure-pipelines.yml",
            ".appveyor.yml",
            "appveyor.yml",
            "jenkinsfile",
            ".drone.yml",
            "bitbucket-pipelines.yml",
        }
        for p in code.files
    )
    has_lock = any(
        p.rsplit("/", 1)[-1].strip().lower()
        in {
            "requirements.txt",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "go.mod",
            "cargo.toml",
        }
        for p in code.files
    )

    # documentation_available IFF a documentation file is present (Req 1.6/1.7).
    assert signals.documentation_available == has_docs
    assert bool(signals.doc_files) == has_docs

    # test_coverage_available IFF a test file OR a CI_Config file is present.
    assert signals.test_coverage_available == (has_tests or has_ci)
    assert bool(signals.test_files) == has_tests
    assert bool(signals.ci_files) == has_ci

    # dependency_available IFF a lockfile is present (Req 4.6).
    assert signals.dependency_available == has_lock
    assert bool(signals.lockfiles) == has_lock

    # consistency_available follows Req 3.6/3.7: false when README has no
    # detectable references OR the code has no languages and no key files.
    code_side_empty = (not code.languages) and (
        not any(p.rsplit("/", 1)[-1].strip() for p in code.key_files)
    )
    if code_side_empty:
        assert signals.consistency_available is False
    else:
        # The detection vocabulary is KNOWN_LANGUAGES + the code's own detected
        # key-file basenames + the Group A tech-reference terms (task 14.3). A
        # Group A term is a *detectable reference* (governing availability)
        # regardless of whether its artifact is present in the file tree; the
        # presence check only affects match/mismatch, not availability (Req 3.6).
        detection_vocabulary = (
            KNOWN_LANGUAGES
            | {
                p.rsplit("/", 1)[-1].strip().lower()
                for p in code.key_files
                if p.rsplit("/", 1)[-1].strip()
            }
            | set(TECH_REFERENCE_TERMS)
        )
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._+#-]*", readme or "")
        references = {
            t.strip("._-").lower()
            for t in tokens
            if t.strip("._-").lower() in detection_vocabulary
        }
        expected_consistency = bool(references)
        assert signals.consistency_available == expected_consistency

# --------------------------------------------------------------------------- #
# Property 10: File classification partitions by pattern
# --------------------------------------------------------------------------- #

# Basenames recognized as dependency manifest / lock files. Mirrors
# ``app.health.signals.LOCKFILE_NAMES`` (all lowercase).
_LOCKFILE_NAMES = frozenset(
    {
        "requirements.txt",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "go.mod",
        "cargo.toml",
    }
)

# Well-known single-file CI configs. Mirrors the basename set in
# ``app.health.signals._is_ci_config_file``.
_CI_BASENAMES = frozenset(
    {
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        ".appveyor.yml",
        "appveyor.yml",
        "jenkinsfile",
        ".drone.yml",
        "bitbucket-pipelines.yml",
    }
)


def _clf_basename(path: str) -> str:
    """Lowercase final path component. Mirrors ``signals._basename``."""
    return path.rsplit("/", 1)[-1].strip().lower()


def _clf_segments(path: str) -> list[str]:
    """Lowercase '/'-separated non-empty segments. Mirrors ``signals._path_segments``."""
    return [seg for seg in path.lower().split("/") if seg]


def _is_doc(path: str) -> bool:
    """Mirror of ``app.health.signals._is_documentation_file``."""
    name = _clf_basename(path)
    segments = _clf_segments(path)
    if name.endswith((".md", ".markdown", ".rst")):
        return True
    if any(seg in ("docs", "doc") for seg in segments[:-1]):
        return True
    return False


def _is_test(path: str) -> bool:
    """Mirror of ``app.health.signals._is_test_file``."""
    name = _clf_basename(path)
    segments = _clf_segments(path)
    if any(seg in ("test", "tests", "spec", "specs", "__tests__") for seg in segments[:-1]):
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.startswith("test_") or stem.startswith("test-"):
        return True
    if stem.endswith("_test") or stem.endswith("-test"):
        return True
    if ".test." in name or ".spec." in name:
        return True
    if stem.endswith(".test") or stem.endswith(".spec"):
        return True
    return False


def _is_ci(path: str) -> bool:
    """Mirror of ``app.health.signals._is_ci_config_file``."""
    lower = path.lower()
    name = _clf_basename(path)
    segments = _clf_segments(path)
    if ".github/workflows/" in lower:
        return True
    if any(seg == ".circleci" for seg in segments):
        return True
    if name in _CI_BASENAMES:
        return True
    return False


def _is_lock(path: str) -> bool:
    """Mirror of ``app.health.signals._is_lockfile``."""
    return _clf_basename(path) in _LOCKFILE_NAMES


@st.composite
def _mixed_file_paths(draw) -> tuple[str, ...]:
    """Generate a list of file paths mixing doc / test / CI / lockfile / other
    patterns.

    Each group is drawn independently (possibly empty) from the shared path
    pools so the resulting tuple exercises every combination — including paths
    that match more than one group (e.g. a ``.md`` file living under a docs
    directory is both documentation, and a ``.yml`` CI file under a ``tests/``
    directory would be both test and CI). Uniqueness is enforced across the whole
    list so per-group set membership is unambiguous.
    """
    docs = draw(st.lists(_DOC_PATHS, min_size=0, max_size=3, unique=True))
    tests = draw(st.lists(_TEST_PATHS, min_size=0, max_size=3, unique=True))
    cis = draw(st.lists(_CI_PATHS, min_size=0, max_size=3, unique=True))
    locks = draw(st.lists(_LOCKFILE_PATHS, min_size=0, max_size=3, unique=True))
    others = draw(st.lists(_OTHER_PATHS, min_size=0, max_size=3, unique=True))
    # Paths that deliberately match more than one group, to prove the groupings
    # are per-pattern membership sets rather than a strict partition.
    multi = draw(
        st.lists(
            st.sampled_from(
                [
                    "docs/test_intro.md",  # doc (.md + docs/) AND test (test_)
                    "tests/config.md",  # doc (.md) AND test (tests/ dir)
                    "spec/setup.py",  # test (spec/ dir) AND lockfile (setup.py)
                    "docs/.gitlab-ci.yml",  # doc (docs/) AND ci (.gitlab-ci.yml)
                ]
            ),
            min_size=0,
            max_size=4,
            unique=True,
        )
    )
    # Deduplicate across all groups while preserving a deterministic order.
    seen: set[str] = set()
    ordered: list[str] = []
    for path in docs + tests + cis + locks + others + multi:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return tuple(ordered)


# Feature: repo-health-score, Property 10: File classification partitions by pattern
@given(files=_mixed_file_paths())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_10_file_classification_partitions_by_pattern(files):
    """For any list of file paths, the documentation, test, CI_Config, and
    lockfile groupings each contain exactly the paths matching their respective
    patterns.

    A path may belong to more than one group (e.g. a ``.md`` file under a docs
    directory), so this asserts set membership per group rather than a strict
    partition of the whole file list.

    **Validates: Requirements 1.3**
    """
    code = StructuredCodeData(
        files=files,
        languages={},
        key_files=(),
        file_count=len(files),
        file_contents={},
    )

    signals = extract_signals("", code)

    expected_docs = {p for p in files if _is_doc(p)}
    expected_tests = {p for p in files if _is_test(p)}
    expected_ci = {p for p in files if _is_ci(p)}
    expected_locks = {p for p in files if _is_lock(p)}

    assert set(signals.doc_files) == expected_docs
    assert set(signals.test_files) == expected_tests
    assert set(signals.ci_files) == expected_ci
    assert set(signals.lockfiles) == expected_locks

    # Each grouping draws only from the input paths (no fabricated entries) and
    # is free of duplicates.
    for group in (signals.doc_files, signals.test_files, signals.ci_files, signals.lockfiles):
        assert set(group).issubset(set(files))
        assert len(group) == len(set(group))

# --------------------------------------------------------------------------- #
# Property 11: Technology-reference recognition is case-insensitive
# --------------------------------------------------------------------------- #

from app.health.signals import (  # noqa: E402
    LOCKFILE_NAMES,
    TECH_REFERENCE_TERMS,
    _derive_consistency,
    _tokenize_readme,
)

# Key-file basenames the code side can advertise as detection vocabulary. These
# are the dotted manifest names recognized alongside KNOWN_LANGUAGES (Req 3.1).
_KEY_FILE_BASENAMES = tuple(sorted(LOCKFILE_NAMES))

# Group A technology-reference terms (task 14.3). These are in the detection
# vocabulary too, so they must be excluded from the "arbitrary / non-vocabulary"
# token pools below — otherwise a generated filler token like "docker" would be
# a genuine detectable reference and break the recognition oracles.
_TECH_TERMS = frozenset(TECH_REFERENCE_TERMS)


def _random_case(draw, word: str) -> str:
    """Re-case ``word`` character-by-character to an arbitrary upper/lower mix.

    Exercises the case-insensitivity requirement: the returned string is equal
    to ``word`` only when compared case-insensitively.
    """
    flags = draw(
        st.lists(st.booleans(), min_size=len(word), max_size=len(word))
    )
    return "".join(
        ch.upper() if flag else ch.lower() for ch, flag in zip(word, flags)
    )


# A vocabulary reference is any known language name or known key-file basename.
_VOCAB_WORDS = tuple(sorted(KNOWN_LANGUAGES)) + _KEY_FILE_BASENAMES


@st.composite
def _recognized_token(draw) -> tuple[str, str]:
    """A known vocabulary word rendered in an arbitrary case.

    Returns ``(rendered_token, canonical_lower)`` where ``canonical_lower`` is
    the word's lowercase form (its identity in the detection vocabulary).
    """
    word = draw(st.sampled_from(_VOCAB_WORDS))
    return _random_case(draw, word), word.lower()


# Non-vocabulary tokens: word-like strings (so they survive tokenization) whose
# lowercased, separator-stripped form is guaranteed NOT to collide with any
# vocabulary word — a known language, a known key-file basename, OR a Group A
# tech-reference term (task 14.3). These must NOT be recognized regardless of
# casing.
_ARBITRARY_TOKEN = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=12,
).filter(
    lambda s: s.strip("._-").lower()
    not in ({w.lower() for w in _VOCAB_WORDS} | _TECH_TERMS)
)


@st.composite
def _unrecognized_token(draw) -> str:
    """A word-like token in arbitrary case that is not in the vocabulary."""
    word = draw(_ARBITRARY_TOKEN)
    return _random_case(draw, word)


# A StructuredCodeData whose languages + key_files establish a NON-EMPTY
# detection vocabulary on the code side, so consistency_available is governed
# solely by whether the README contributes a detectable reference (Req 3.6/3.7).
# Every KNOWN_LANGUAGE is advertised as present, and every known key-file
# basename is advertised as a key file, so any vocabulary token in the README
# is guaranteed to also be a *match* (present in the code structure).
_FULL_VOCABULARY_CODE = StructuredCodeData(
    files=(),
    languages={lang: 1 for lang in sorted(KNOWN_LANGUAGES)},
    key_files=_KEY_FILE_BASENAMES,
    file_count=0,
    file_contents={},
)


def _is_recognized(token: str) -> bool:
    """Recognition oracle: a token is a detectable reference iff its tokenized
    (separator-stripped, lowercased) form is in KNOWN_LANGUAGES, a known
    key-file basename, or a Group A tech-reference term — compared
    case-insensitively (Req 3.1, task 14.3).
    """
    normalized = _tokenize_readme(token)
    if not normalized:
        return False
    canonical = normalized[0]
    vocabulary = (
        KNOWN_LANGUAGES | {name.lower() for name in _KEY_FILE_BASENAMES} | _TECH_TERMS
    )
    return canonical in vocabulary


# Feature: repo-health-score, Property 11: Technology-reference recognition is case-insensitive
@given(
    recognized=_recognized_token(),
    unrecognized=_unrecognized_token(),
    data=st.data(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_11_technology_reference_recognition_is_case_insensitive(
    recognized, unrecognized, data
):
    """For any README token, it is recognized as a detectable technology
    reference if and only if it matches a known language name or known key-file
    name when compared case-insensitively.

    This is checked from both directions, exercised through the public
    ``extract_signals`` seam (``_derive_consistency``) against a code structure
    whose detection vocabulary is non-empty (so availability is governed solely
    by the README side, Req 3.6):

    * A known vocabulary word embedded in a README in ARBITRARY case is
      recognized — ``consistency_available`` is ``True`` — and its casing does
      not change the outcome (any other casing of the same word is recognized
      too).
    * A word-like token that is NOT in the vocabulary is not recognized in ANY
      casing — a README containing only that token yields
      ``consistency_available`` ``False``.

    **Validates: Requirements 3.1**
    """
    recognized_token, canonical = recognized

    # --- Sanity: the oracle agrees with the generators. --- #
    assert _is_recognized(recognized_token) is True
    assert _is_recognized(unrecognized) is False

    # --- Case-insensitivity: recognition is independent of the specific
    # casing. The same vocabulary word in any other random case is recognized
    # identically (both True). --- #
    other_casing = _random_case(data.draw, canonical)
    assert _is_recognized(recognized_token) == _is_recognized(other_casing) is True

    # --- IFF via extract_signals: a README whose only token is recognized makes
    # a detectable reference available; an unrecognized-only README does not. The
    # code side advertises a full, non-empty vocabulary so the code-side
    # availability guard (Req 3.7) never fires and only the README's token
    # governs the flag. --- #
    readme_recognized = f"Built with {recognized_token} today."
    _, available_recognized = _derive_consistency(
        readme_recognized,
        dict(_FULL_VOCABULARY_CODE.languages),
        _FULL_VOCABULARY_CODE.key_files,
        _FULL_VOCABULARY_CODE.files,
    )
    assert available_recognized is True

    readme_unrecognized = f"Built with {unrecognized} today."
    _, available_unrecognized = _derive_consistency(
        readme_unrecognized,
        dict(_FULL_VOCABULARY_CODE.languages),
        _FULL_VOCABULARY_CODE.key_files,
        _FULL_VOCABULARY_CODE.files,
    )
    assert available_unrecognized is False

    # --- The full signals object confirms the same recognition semantics end
    # to end (through the public entry point). --- #
    signals_recognized = extract_signals(readme_recognized, _FULL_VOCABULARY_CODE)
    assert signals_recognized.consistency_available is True

    signals_unrecognized = extract_signals(readme_unrecognized, _FULL_VOCABULARY_CODE)
    assert signals_unrecognized.consistency_available is False

# --------------------------------------------------------------------------- #
# Property 12: Detectable references are deduplicated case-insensitively
# --------------------------------------------------------------------------- #

from hypothesis import assume  # noqa: E402

# Two disjoint pools of detectable vocabulary words:
#
# * ``_LANGUAGE_WORDS`` — known language names. A language word becomes a
#   *match* when it is advertised in the code's ``languages`` map, and a
#   *mismatch* (detectable but not matched) when it is left out of ``languages``
#   while still being part of KNOWN_LANGUAGES (hence still in the detection
#   vocabulary, Req 3.1).
# * ``_KEYFILE_WORDS`` — known key-file basenames. Advertised via ``key_files``
#   to become matches.
#
# Splitting the pools this way lets a case build a KNOWN, exact matches/total
# split so the ratio's denominator can be asserted directly. The denominator is
# the number of DISTINCT case-insensitive references — this is what proves the
# case-insensitive dedup (Req 3.2): repeating one word in many casings must not
# inflate the denominator.
_LANGUAGE_WORDS = tuple(sorted(KNOWN_LANGUAGES))
_KEYFILE_WORDS = _KEY_FILE_BASENAMES  # already lowercase basenames


@st.composite
def _dedup_case(draw):
    """Build a README that repeats a chosen set of distinct vocabulary words,
    each in several arbitrary casings, plus a StructuredCodeData advertising a
    known subset of them as matches.

    Returns ``(readme, code, expected_unique, expected_matches)`` where
    ``expected_unique`` is the number of DISTINCT case-insensitive references
    (regardless of how many casings each appears in) and ``expected_matches`` is
    how many of those are advertised by the code side.
    """
    # Distinct match words: some languages advertised in ``languages`` and some
    # key files advertised in ``key_files``. At least one match word is required
    # so the code side advertises a non-empty vocabulary — this keeps the Req 3.7
    # code-side availability guard from firing, isolating the case-insensitive
    # dedup behaviour (Req 3.2) this property is about.
    match_langs = draw(
        st.lists(st.sampled_from(_LANGUAGE_WORDS), min_size=0, max_size=4, unique=True)
    )
    match_keyfiles = draw(
        st.lists(st.sampled_from(_KEYFILE_WORDS), min_size=0, max_size=3, unique=True)
    )
    match_words = list(dict.fromkeys(match_langs + match_keyfiles))
    assume(len(match_words) >= 1)

    # Distinct mismatch words: KNOWN_LANGUAGES that are detectable (in the
    # vocabulary) but deliberately NOT advertised as present, so they count as
    # references but not matches. Exclude any word already used as a match.
    available_mismatch = [w for w in _LANGUAGE_WORDS if w not in set(match_langs)]
    mismatch_words = draw(
        st.lists(st.sampled_from(available_mismatch), min_size=0, max_size=4, unique=True)
    ) if available_mismatch else []
    mismatch_words = [w for w in dict.fromkeys(mismatch_words) if w not in match_words]

    all_words = match_words + mismatch_words
    # Ensure at least one detectable reference so the README side is available
    # (Req 3.6) and the denominator is non-zero.
    assume(len(all_words) >= 1)

    # Render each distinct word in several arbitrary casings, then shuffle the
    # whole token stream so casings and words are interleaved. Repeating a word
    # in K casings must collapse to a single unique reference.
    tokens: list[str] = []
    for word in all_words:
        k = draw(st.integers(min_value=1, max_value=5))
        for _ in range(k):
            tokens.append(_random_case(draw, word))
    tokens = draw(st.permutations(tokens))

    readme = "This project uses " + " ".join(tokens) + " and more."

    code = StructuredCodeData(
        files=(),
        # Advertise exactly the match languages as present.
        languages={lang: 1 for lang in match_langs},
        # Advertise exactly the match key files (as basenames / paths).
        key_files=tuple(match_keyfiles),
        file_count=0,
        file_contents={},
    )
    return readme, code, len(all_words), len(match_words)


# Feature: repo-health-score, Property 12: Detectable references are deduplicated case-insensitively
@given(case=_dedup_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_12_detectable_references_deduplicated_case_insensitively(case):
    """For any README text, the derived set of unique detectable technology
    references contains no two references equal when compared
    case-insensitively.

    This is exercised through ``_derive_consistency`` (the public
    ``extract_signals`` seam). Each distinct vocabulary word is repeated in
    several arbitrary casings; because references are deduplicated
    case-insensitively (Req 3.2), the number of unique references equals the
    number of DISTINCT case-insensitive words — NOT the number of casings. That
    unique count is the denominator of ``consistency_ratio``, so we assert the
    ratio equals ``matches / distinct_words``. Idempotence of casing is asserted
    directly: collapsing every word to a single canonical casing yields an
    identical ratio.

    **Validates: Requirements 3.2**
    """
    readme, code, expected_unique, expected_matches = case

    ratio, available = _derive_consistency(
        readme, dict(code.languages), code.key_files, code.files
    )

    # A detectable reference exists (all words are in the vocabulary) and the
    # code side is non-empty when there is at least one match; otherwise the
    # mismatch-only case still has references so availability holds either way.
    assert available is True

    # The denominator is the count of DISTINCT case-insensitive references. If
    # dedup were case-SENSITIVE, repeating a word in K casings would inflate the
    # denominator and the ratio would differ from matches / distinct_words.
    expected_ratio = expected_matches / expected_unique
    assert math.isclose(ratio, expected_ratio, rel_tol=1e-9, abs_tol=1e-12), (
        f"ratio {ratio!r} != expected {expected_ratio!r} "
        f"(matches={expected_matches}, distinct references={expected_unique}); "
        f"a differing ratio would indicate references were NOT deduplicated "
        f"case-insensitively"
    )

    # The ratio is a proper fraction over the unique-reference count, so its
    # denominator (unique references) must be exactly the distinct-word count.
    assert 0.0 <= ratio <= 1.0

    # --- Idempotence of casing (direct dedup witness) --- #
    # Collapsing every token in the README to its lowercase canonical form must
    # not change the result: the case-insensitive reference set, and therefore
    # the ratio, is invariant under re-casing.
    canonical_readme = readme.lower()
    canonical_ratio, canonical_available = _derive_consistency(
        canonical_readme, dict(code.languages), code.key_files, code.files
    )
    assert canonical_available is available
    assert math.isclose(canonical_ratio, ratio, rel_tol=1e-9, abs_tol=1e-12), (
        "re-casing the README changed the consistency ratio, so references "
        "were not deduplicated case-insensitively"
    )

# --------------------------------------------------------------------------- #
# Property 13: Matches and mismatches partition the reference set
# --------------------------------------------------------------------------- #


@st.composite
def _partition_case(draw):
    """Build a README containing a known set of distinct detectable vocabulary
    words, split by construction into a MATCH set and a MISMATCH set, plus a
    StructuredCodeData that advertises exactly the match words.

    * MATCH words are drawn from known language names (advertised in
      ``languages``) and known key-file basenames (advertised in ``key_files``),
      so each is a detected language or detected key file and therefore a
      consistency match (Req 3.3).
    * MISMATCH words are KNOWN_LANGUAGES that stay in the detection vocabulary
      (Req 3.1) but are deliberately NOT advertised on the code side, so each is
      a detectable reference that does not match (Req 3.4).

    At least one match word is required so the code-side availability guard
    (Req 3.7) never fires, and at least one reference exists overall (Req 3.6).

    Returns ``(readme, code, match_words, mismatch_words)`` where the two word
    lists are the DISTINCT (case-insensitive) references of each partition.
    """
    # --- MATCH words: languages advertised in ``languages`` + key files
    # advertised in ``key_files``. Guaranteed non-empty. --- #
    match_langs = draw(
        st.lists(st.sampled_from(_LANGUAGE_WORDS), min_size=0, max_size=4, unique=True)
    )
    match_keyfiles = draw(
        st.lists(st.sampled_from(_KEYFILE_WORDS), min_size=0, max_size=3, unique=True)
    )
    match_words = list(dict.fromkeys(match_langs + match_keyfiles))
    assume(len(match_words) >= 1)

    # --- MISMATCH words: detectable KNOWN_LANGUAGES NOT advertised on the code
    # side. Exclude any word used as a match (a word advertised as a language
    # would otherwise be a match, not a mismatch). --- #
    available_mismatch = [w for w in _LANGUAGE_WORDS if w not in set(match_langs)]
    mismatch_words = (
        draw(
            st.lists(
                st.sampled_from(available_mismatch),
                min_size=0,
                max_size=4,
                unique=True,
            )
        )
        if available_mismatch
        else []
    )
    mismatch_words = [w for w in dict.fromkeys(mismatch_words) if w not in match_words]

    all_words = match_words + mismatch_words
    assume(len(all_words) >= 1)  # at least one detectable reference (Req 3.6)

    # Render each distinct word once (dedup is Property 12's concern here), in an
    # arbitrary casing, and shuffle so matches/mismatches are interleaved.
    tokens = [_random_case(draw, word) for word in all_words]
    tokens = draw(st.permutations(tokens))
    readme = "This project uses " + " ".join(tokens) + " and more."

    code = StructuredCodeData(
        files=(),
        languages={lang: 1 for lang in match_langs},
        key_files=tuple(match_keyfiles),
        file_count=0,
        file_contents={},
    )
    return readme, code, match_words, mismatch_words


# Feature: repo-health-score, Property 13: Matches and mismatches partition the reference set
@given(case=_partition_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_13_matches_and_mismatches_partition_the_reference_set(case):
    """For any set of unique detectable references and any detected code
    structure, the consistency matches and consistency mismatches are disjoint
    and their combined count equals the total number of unique detectable
    references; a reference is a match exactly when it corresponds to a detected
    language or detected key file.

    Exercised through ``_derive_consistency`` (the public ``extract_signals``
    seam). The README is built from a MATCH set (advertised in the code's
    ``languages``/``key_files``) and a disjoint MISMATCH set (detectable known
    languages left unadvertised). Because ``ratio = matches / total_unique``, the
    numerator ``matches`` is recovered as ``round(ratio * total)``.

    **Validates: Requirements 3.3, 3.4**
    """
    readme, code, match_words, mismatch_words = case

    expected_matches = len(match_words)
    expected_mismatches = len(mismatch_words)
    expected_unique = expected_matches + expected_mismatches

    ratio, available = _derive_consistency(
        readme, dict(code.languages), code.key_files, code.files
    )

    # There is at least one reference and at least one match word, so the code
    # side advertises a non-empty vocabulary — availability holds (Req 3.6/3.7).
    assert available is True

    # --- Disjointness of the partition, by construction. No word is both a
    # match and a mismatch (they are drawn from disjoint pools). --- #
    assert set(match_words).isdisjoint(set(mismatch_words))

    # --- Combined count equals the total number of unique detectable
    # references. ``ratio = matches / total``; recover matches and total. --- #
    # ``total`` is the count of unique detectable references; every generated
    # word is a distinct vocabulary reference, so total == expected_unique.
    recovered_matches = round(ratio * expected_unique)
    assert recovered_matches == expected_matches, (
        f"recovered matches {recovered_matches} != expected {expected_matches} "
        f"(ratio={ratio!r}, total={expected_unique}); matches + mismatches must "
        f"equal the unique-reference count"
    )
    # matches + mismatches == total unique references.
    assert expected_matches + expected_mismatches == expected_unique

    # --- A reference is a match EXACTLY when advertised in code: move one word
    # between the partitions and observe the numerator change accordingly. --- #
    if mismatch_words:
        # Promote a mismatch word to a match by advertising it as a language.
        promoted = mismatch_words[0]
        promoted_langs = dict(code.languages)
        promoted_langs[promoted] = 1
        promoted_ratio, promoted_available = _derive_consistency(
            readme, promoted_langs, code.key_files, code.files
        )
        assert promoted_available is True
        promoted_matches = round(promoted_ratio * expected_unique)
        assert promoted_matches == expected_matches + 1, (
            "advertising a previously-unmatched reference did not turn it into a "
            "match (the numerator should increase by exactly one)"
        )

    if len(match_words) >= 1 and match_words[0] in code.languages:
        # Demote a matched language by removing it from the advertised code
        # structure; it stays a detectable reference (KNOWN_LANGUAGES) but is no
        # longer a match, so the numerator drops by one.
        demoted = match_words[0]
        demoted_langs = dict(code.languages)
        del demoted_langs[demoted]
        # Keep the code side non-empty so availability is preserved: retain the
        # advertised key files, and if none, ensure another language remains.
        if demoted_langs or code.key_files:
            demoted_ratio, demoted_available = _derive_consistency(
                readme, demoted_langs, code.key_files, code.files
            )
            if demoted_available:
                demoted_matches = round(demoted_ratio * expected_unique)
                assert demoted_matches == expected_matches - 1, (
                    "un-advertising a matched reference did not turn it into a "
                    "mismatch (the numerator should decrease by exactly one)"
                )

    # --- Ratio is a proper fraction over the unique-reference count. --- #
    assert 0.0 <= ratio <= 1.0

# --------------------------------------------------------------------------- #
# Property 14: Consistency ratio is well-defined and in range
# --------------------------------------------------------------------------- #


# Feature: repo-health-score, Property 14: Consistency ratio is well-defined and in range
@given(case=_partition_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_14_consistency_ratio_is_well_defined_and_in_range(case):
    """For any set of unique detectable references and detected code structure
    with at least one reference, the README-vs-code consistency ratio equals
    ``matches / total_unique`` and lies in the range ``[0.0, 1.0]`` inclusive.

    Exercised through ``_derive_consistency`` (the public ``extract_signals``
    seam). ``_partition_case`` builds a README from a MATCH set (advertised in
    the code's ``languages``/``key_files``) and a disjoint MISMATCH set
    (detectable known languages left unadvertised), guaranteeing at least one
    detectable reference and a non-empty code-side vocabulary — so availability
    holds (Req 3.6/3.7) and the ratio is well-defined.

    Boundary coverage arises naturally from the generator: a case with no
    mismatch words yields all matches (ratio 1.0); constructing the all-mismatch
    companion (below) yields ratio 0.0 while the code side stays non-empty.

    **Validates: Requirements 3.5**
    """
    readme, code, match_words, mismatch_words = case

    expected_matches = len(match_words)
    expected_total = len(match_words) + len(mismatch_words)

    ratio, available = _derive_consistency(
        readme, dict(code.languages), code.key_files, code.files
    )

    # At least one reference and at least one match word are guaranteed by the
    # generator, so the code side advertises a non-empty vocabulary and there is
    # a detectable reference — availability holds (Req 3.6/3.7) and the ratio is
    # well-defined.
    assert available is True

    # (a) The ratio equals matches / total_unique within float tolerance. --- #
    assert expected_total >= 1  # guaranteed by the generator (at least one ref)
    expected_ratio = expected_matches / expected_total
    assert math.isclose(ratio, expected_ratio, rel_tol=1e-9, abs_tol=1e-12), (
        f"ratio {ratio!r} != matches/total {expected_matches}/{expected_total} "
        f"= {expected_ratio!r}"
    )

    # (b) The ratio lies in [0.0, 1.0] inclusive. --- #
    assert 0.0 <= ratio <= 1.0

    # --- Boundary: all matches -> exactly 1.0. When the generator produced no
    # mismatch words, every unique reference is a detected match. --- #
    if not mismatch_words:
        assert ratio == 1.0

    # --- Boundary: all mismatches -> exactly 0.0. Build a companion README from
    # ONLY the mismatch words (detectable known languages that the code does not
    # advertise) while keeping the SAME non-empty code structure, so the code
    # side stays non-empty (availability True) but no reference matches. --- #
    if mismatch_words:
        mismatch_only_readme = (
            "This project uses " + " ".join(mismatch_words) + " and more."
        )
        zero_ratio, zero_available = _derive_consistency(
            mismatch_only_readme, dict(code.languages), code.key_files, code.files
        )
        # The code side (languages/key_files from the matched words) is still
        # non-empty, and the README still has detectable references (the
        # mismatch known-language words), so availability holds.
        assert zero_available is True
        assert zero_ratio == 0.0
        assert 0.0 <= zero_ratio <= 1.0

# --------------------------------------------------------------------------- #
# Property 15: Consistency availability conditions
# --------------------------------------------------------------------------- #

# A README made ONLY of non-vocabulary tokens: each word-like token is
# guaranteed not to collide with any known language or key-file basename, in an
# arbitrary casing (so the "no detectable reference" condition of Req 3.6 is
# genuine and casing-independent).
@st.composite
def _non_vocabulary_readme(draw) -> str:
    tokens = draw(st.lists(_ARBITRARY_TOKEN, min_size=1, max_size=8))
    cased = [_random_case(draw, tok) for tok in tokens]
    # Interleave plain punctuation/filler that also carries no reference.
    return "This uses " + " ".join(cased) + " here."


# A non-empty code detection vocabulary: at least one language and/or key file,
# so the code-side availability guard (Req 3.7) does NOT fire and availability
# is governed solely by the README side (isolating Req 3.6).
@st.composite
def _non_empty_code_vocab(draw) -> tuple[dict[str, int], tuple[str, ...]]:
    langs = draw(
        st.lists(st.sampled_from(_LANGUAGE_WORDS), min_size=0, max_size=4, unique=True)
    )
    keyfiles = draw(
        st.lists(st.sampled_from(_KEYFILE_WORDS), min_size=0, max_size=3, unique=True)
    )
    # Force the combined code vocabulary to be non-empty.
    if not langs and not keyfiles:
        langs = [draw(st.sampled_from(_LANGUAGE_WORDS))]
    languages = {lang: 1 for lang in langs}
    key_files = tuple(keyfiles)
    return languages, key_files


# A README that contains at least one detectable vocabulary reference, in
# arbitrary casing, mixed with optional non-vocabulary filler. References are
# drawn from ``words`` — a caller-supplied pool of terms known to be in the
# detection vocabulary for the intended scenario. For the code-empty scenario
# (Req 3.7) any vocabulary word is detectable via the system-known set; for the
# positive case the caller must pass words that the code side also advertises.
@st.composite
def _vocabulary_readme(draw, words: tuple[str, ...]) -> str:
    refs = draw(st.lists(st.sampled_from(words), min_size=1, max_size=5, unique=True))
    filler = draw(st.lists(_ARBITRARY_TOKEN, min_size=0, max_size=4))
    tokens = [_random_case(draw, w) for w in (list(refs) + filler)]
    tokens = draw(st.permutations(tokens))
    return "Project uses " + " ".join(tokens) + " and more."


@st.composite
def _availability_case(draw) -> tuple[str, dict[str, int], tuple[str, ...], bool]:
    """Build one of the two availability scenarios plus an optional positive case.

    Returns ``(readme, languages, key_files, expected_available)``.

    * ``readme_no_reference`` (Req 3.6): a non-vocabulary-only README with a
      NON-EMPTY code vocabulary -> availability must be False (the README side
      contributes nothing detectable even though the code side is present).
    * ``code_empty`` (Req 3.7): a vocabulary-containing README with EMPTY code
      (``languages={}``, ``key_files=()``) -> availability must be False (the
      code side has nothing to be consistent with, even though the README does).
    * ``both_present``: a vocabulary README with non-empty code -> availability
      must be True (both guards satisfied).
    """
    scenario = draw(
        st.sampled_from(("readme_no_reference", "code_empty", "both_present"))
    )

    if scenario == "readme_no_reference":
        readme = draw(_non_vocabulary_readme())
        languages, key_files = draw(_non_empty_code_vocab())
        return readme, languages, key_files, False

    if scenario == "code_empty":
        # Known-language words are always in the system detection vocabulary, so
        # the README genuinely carries detectable references; the code-side
        # guard (Req 3.7) is what forces availability False.
        readme = draw(_vocabulary_readme(_LANGUAGE_WORDS))
        return readme, {}, (), False

    # both_present: the README must reference the code's OWN detection
    # vocabulary. Known languages are always detectable; advertised key-file
    # basenames become detectable only because the code advertises them. Draw
    # the README references from exactly what this code structure exposes.
    languages, key_files = draw(_non_empty_code_vocab())
    code_vocab = tuple(languages.keys()) + key_files
    readme = draw(_vocabulary_readme(code_vocab))
    return readme, languages, key_files, True


# Feature: repo-health-score, Property 15: Consistency availability conditions
@given(case=_availability_case())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_15_consistency_availability_conditions(case):
    """For any README text and code structure, the consistency availability flag
    is false when the README contains no detectable technology references, and
    false when the code structure contains no languages and no key files.

    Exercised through ``_derive_consistency`` (the public ``extract_signals``
    seam):

    * README with NO detectable references + non-empty code vocabulary
      -> availability False (Req 3.6).
    * Vocabulary-containing README + empty code (no languages, no key files)
      -> availability False (Req 3.7).
    * Vocabulary README + non-empty code -> availability True (both guards
      satisfied), confirming the flags above are not vacuously false.

    **Validates: Requirements 3.6, 3.7**
    """
    readme, languages, key_files, expected_available = case

    # These scenarios carry no code-side tech artifacts, so ``files`` is empty:
    # Group A terms would never match, and none is generated as a reference here
    # (the arbitrary/non-vocabulary token pools exclude tech-reference terms).
    _, available = _derive_consistency(readme, dict(languages), key_files, ())
    assert available is expected_available

# --------------------------------------------------------------------------- #
# Property 18: Dependency counts aggregate across lockfiles
# --------------------------------------------------------------------------- #


@st.composite
def _labelled_multi_lockfiles(draw):
    """Generate a collection of MULTIPLE lockfiles at distinct paths.

    Each lockfile is rendered from the labelled generators (reused from
    Properties 16/17), which carry a known per-file ``(total, unpinned)`` split.
    ``file_contents`` is keyed by path, so each lockfile is placed at a UNIQUE
    path (e.g. ``requirements.txt``, ``sub/requirements.txt``, ``package.json``)
    to guarantee its content is not clobbered.

    Returns ``(files, file_contents, expected_total, expected_unpinned)`` where
    the two expected values are the SUMS of the per-file totals and unpinned
    counts respectively.
    """
    count = draw(st.integers(min_value=1, max_value=6))

    files: list[str] = []
    file_contents: dict[str, str] = {}
    expected_total = 0
    expected_unpinned = 0

    for index in range(count):
        fmt = draw(st.sampled_from(["requirements.txt", "package.json"]))
        if fmt == "requirements.txt":
            content, total, unpinned = draw(_labelled_requirements_txt())
        else:
            content, total, unpinned = draw(_labelled_package_json())

        # Distinct path per lockfile so file_contents (keyed by path) never
        # collides. The basename stays a recognized lockfile name so the file is
        # classified as a lockfile by extract_signals.
        path = fmt if index == 0 else f"sub{index}/{fmt}"

        files.append(path)
        file_contents[path] = content
        expected_total += total
        expected_unpinned += unpinned

    return tuple(files), file_contents, expected_total, expected_unpinned


# Feature: repo-health-score, Property 18: Dependency counts aggregate across lockfiles
@given(case=_labelled_multi_lockfiles())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_18_dependency_counts_aggregate_across_lockfiles(case):
    """For any collection of detected lockfiles, the recorded
    ``dependency_total`` and ``dependency_unpinned`` equal the sums of the
    per-lockfile totals and unpinned counts respectively.

    A ``StructuredCodeData`` is built whose ``files`` includes every generated
    lockfile path and whose ``file_contents`` maps each path to its content.
    ``extract_signals`` classifies all of them as lockfiles and aggregates their
    pinning counts; the aggregated totals must equal the sums of the known
    per-file ``(total, unpinned)`` values.

    The per-file expectations are cross-checked against ``parse_pinning`` on each
    content so the assertion is independent of how the labels were generated.

    **Validates: Requirements 4.4**
    """
    files, file_contents, expected_total, expected_unpinned = case

    # Cross-check the generator's labels against parse_pinning per file, so the
    # aggregate expectation is grounded in the parser's own per-file results.
    parsed_total = 0
    parsed_unpinned = 0
    for path in files:
        pinning = parse_pinning(path, file_contents[path])
        parsed_total += pinning.total
        parsed_unpinned += pinning.unpinned
    assert parsed_total == expected_total
    assert parsed_unpinned == expected_unpinned

    code = StructuredCodeData(
        files=files,
        languages={},
        key_files=(),
        file_count=len(files),
        file_contents=file_contents,
    )

    signals = extract_signals("", code)

    # Every generated path is a recognized lockfile, so all are detected.
    assert set(signals.lockfiles) == set(files)
    assert signals.dependency_available is True

    # The aggregated counts equal the sums of the per-file totals / unpinned.
    assert signals.dependency_total == expected_total
    assert signals.dependency_unpinned == expected_unpinned
# --------------------------------------------------------------------------- #
# Property 19: Dependency availability requires a lockfile
# --------------------------------------------------------------------------- #

# Non-lockfile path pools whose basenames are GUARANTEED not to be recognized
# lockfile names (``_LOCKFILE_NAMES``). Every basename below is checked against
# the predicate in the sanity assertions of the test, so the "no detected
# lockfile" precondition of Property 19 holds by construction.
_NON_LOCKFILE_POOL = st.one_of(
    _DOC_PATHS,
    _TEST_PATHS,
    _CI_PATHS,
    _OTHER_PATHS,
)


@st.composite
def _no_lockfile_code(draw) -> StructuredCodeData:
    """Generate a ``StructuredCodeData`` whose ``files`` contain NO recognized
    lockfile — only documentation / test / CI / other non-lockfile paths.

    Every drawn path's basename is verified (in the test body) to be absent from
    ``_LOCKFILE_NAMES``, so ``extract_signals`` detects zero lockfiles.
    """
    files = tuple(
        draw(st.lists(_NON_LOCKFILE_POOL, min_size=0, max_size=8, unique=True))
    )
    return StructuredCodeData(
        files=files,
        languages={},
        key_files=(),
        file_count=len(files),
        file_contents={},
    )


# Feature: repo-health-score, Property 19: Dependency availability requires a lockfile
@given(code=_no_lockfile_code(), lock_path=_LOCKFILE_PATHS)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_19_dependency_availability_requires_a_lockfile(code, lock_path):
    """For any code structure containing no detected lockfile, the
    Dependency_Freshness signal availability flag is false (and the aggregated
    dependency counts are zero).

    The converse sanity check confirms that once at least one recognized
    lockfile path is present, ``dependency_available`` flips to true — so the
    flag is genuinely governed by lockfile presence rather than being constant.

    **Validates: Requirements 4.6**
    """
    # --- Sanity: the generator truly produced NO recognized lockfile. --- #
    assert all(_clf_basename(p) not in _LOCKFILE_NAMES for p in code.files)

    signals = extract_signals("", code)

    # No lockfile detected -> availability false and counts zero (Req 4.6).
    assert signals.lockfiles == ()
    assert signals.dependency_available is False
    assert signals.dependency_total == 0
    assert signals.dependency_unpinned == 0

    # --- Converse: adding a single recognized lockfile flips availability. --- #
    assert _clf_basename(lock_path) in _LOCKFILE_NAMES
    with_lock = StructuredCodeData(
        files=code.files + (lock_path,),
        languages={},
        key_files=(),
        file_count=len(code.files) + 1,
        file_contents={},
    )
    signals_with_lock = extract_signals("", with_lock)
    assert lock_path in signals_with_lock.lockfiles
    assert signals_with_lock.dependency_available is True

# --------------------------------------------------------------------------- #
# Scoring generators (shared by Property 7.x scoring tests)
# --------------------------------------------------------------------------- #
#
# ``_repository_signals`` builds an ARBITRARY RepositorySignals directly (rather
# than through ``extract_signals``) so scoring properties can explore the entire
# signal space — including combinations the extractor might never produce — such
# as arbitrary ReadmeMetrics, arbitrary file tuples, arbitrary dependency counts
# and any combination of availability flags. Later Property 7.x tests reuse this
# generator.

from app.health.models import (  # noqa: E402
    CategoryScore,
    HealthScoreResult,
    ReadmeMetrics,
    RepositorySignals,
)
from app.health.scoring import compute_health_score  # noqa: E402

# A small pool of plausible file paths, reused for the doc/test/ci/lockfile/
# key_file tuples. Exact paths do not matter for the range property — only the
# counts influence scoring — so a compact pool keeps generation fast.
_ANY_PATHS = st.lists(
    st.text(alphabet=_NAME_ALPHABET + "/._-", min_size=1, max_size=20).filter(
        lambda s: s.strip("/") != ""
    ),
    min_size=0,
    max_size=6,
    unique=True,
)


@st.composite
def _readme_metrics(draw) -> ReadmeMetrics:
    """Arbitrary ReadmeMetrics: non-negative counts and arbitrary section flags."""
    return ReadmeMetrics(
        length=draw(st.integers(min_value=0, max_value=100_000)),
        heading_count=draw(st.integers(min_value=0, max_value=50)),
        code_block_count=draw(st.integers(min_value=0, max_value=50)),
        has_install_section=draw(st.booleans()),
        has_usage_section=draw(st.booleans()),
    )


@st.composite
def _repository_signals(draw) -> RepositorySignals:
    """Generate an ARBITRARY RepositorySignals covering the full signal space.

    Every field is drawn independently: arbitrary ReadmeMetrics, arbitrary
    doc/test/ci/lockfile/key_file path tuples, an arbitrary languages map,
    arbitrary dependency totals/unpinned counts, a ``consistency_ratio`` in
    ``[0.0, 1.0]``, and every per-category availability flag drawn independently.
    This deliberately includes states the extractor may never emit (e.g. a
    non-empty ``doc_files`` while ``documentation_available`` is False), so the
    scoring core is exercised across its entire input domain.
    """
    total = draw(st.integers(min_value=0, max_value=500))
    # unpinned is drawn across [0, total] AND beyond it, so the scorer's
    # defensive clamping of unpinned into [0, total] is exercised too.
    unpinned = draw(st.integers(min_value=0, max_value=max(total, 0) + 50))

    languages = draw(
        st.dictionaries(
            keys=st.sampled_from(sorted(KNOWN_LANGUAGES)),
            values=st.integers(min_value=1, max_value=100),
            max_size=6,
        )
    )

    return RepositorySignals(
        readme_text=draw(st.text(max_size=200)),
        readme_metrics=draw(_readme_metrics()),
        doc_files=tuple(draw(_ANY_PATHS)),
        test_files=tuple(draw(_ANY_PATHS)),
        ci_files=tuple(draw(_ANY_PATHS)),
        lockfiles=tuple(draw(_ANY_PATHS)),
        languages=languages,
        key_files=tuple(draw(_ANY_PATHS)),
        dependency_total=total,
        dependency_unpinned=unpinned,
        consistency_ratio=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        readme_available=draw(st.booleans()),
        documentation_available=draw(st.booleans()),
        test_coverage_available=draw(st.booleans()),
        dependency_available=draw(st.booleans()),
        consistency_available=draw(st.booleans()),
    )


# A valid, fully-populated weight config (all five categories, non-negative,
# finite, positive sum). Reuses the vetted generator from Property 23; we take
# only the config (dropping the scale factor) so scoring gets a valid config.
@st.composite
def _valid_weight_config(draw) -> dict:
    """A valid weight config accepted by ``normalize_weights`` (positive sum)."""
    config, _scale = draw(_valid_weight_config_and_scale())
    return config


# The weights argument passed to ``compute_health_score``: either ``None`` (use
# the configured defaults) or an arbitrary valid config.
_WEIGHTS_ARG = st.one_of(st.none(), _valid_weight_config())


# --------------------------------------------------------------------------- #
# Property 1: Every category score is within range
# --------------------------------------------------------------------------- #


# Feature: repo-health-score, Property 1: Every category score is within range
@given(signals=_repository_signals(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_1_every_category_score_is_within_range(signals, weights):
    """For any RepositorySignals and any valid Weight_Config, every CategoryScore
    in the resulting HealthScoreResult is an integer in the range 0 to 100
    inclusive, and the result contains exactly the five categories.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**
    """
    result = compute_health_score(signals, weights)

    # Exactly the five categories are present (no more, no fewer), each exactly
    # once (Req 2.1-2.5 each define one category score).
    assert len(result.categories) == 5
    assert {cs.category for cs in result.categories} == set(Category)

    for category_score in result.categories:
        assert isinstance(category_score, CategoryScore)
        score = category_score.score
        # A bool is an int subclass; a score must be a genuine int (Req 5.3).
        assert isinstance(score, int) and not isinstance(score, bool), (
            f"category {category_score.category.value!r} score is not an int: "
            f"{score!r}"
        )
        assert 0 <= score <= 100, (
            f"category {category_score.category.value!r} score {score} is outside "
            f"the range [0, 100]"
        )

# --------------------------------------------------------------------------- #
# Property 2: README quality is monotonic in completeness
# --------------------------------------------------------------------------- #
#
# README_Quality depends ONLY on ``readme_metrics`` and ``readme_available``
# (see ``app.health.scoring._score_readme_quality``). To isolate the
# monotonicity guarantee we build two RepositorySignals that are IDENTICAL in
# every field except ``readme_metrics`` (both ``readme_available=True``), where
# the second ``ReadmeMetrics`` DOMINATES the first in every completeness
# dimension. The property then asserts the README_Quality category score for the
# dominating metrics is greater than or equal to that for the base metrics.


def _readme_quality_score(result: HealthScoreResult) -> int:
    """Extract the README_Quality CategoryScore value from a HealthScoreResult."""
    for category_score in result.categories:
        if category_score.category is Category.README_QUALITY:
            return category_score.score
    raise AssertionError("result did not contain a README_Quality category score")


@st.composite
def _dominating_readme_metrics_pair(draw) -> tuple[ReadmeMetrics, ReadmeMetrics]:
    """Generate a base ``m1`` and a dominating ``m2``.

    ``m2`` dominates ``m1`` in every completeness dimension:
    ``length``, ``heading_count`` and ``code_block_count`` are each grown by a
    non-negative delta, and the boolean ``has_install_section`` /
    ``has_usage_section`` flags satisfy ``m2 >= m1`` (i.e. once ``m1`` sets a
    flag ``m2`` keeps it set; ``True >= False`` and ``True >= True`` hold).
    """
    length1 = draw(st.integers(min_value=0, max_value=100_000))
    heading1 = draw(st.integers(min_value=0, max_value=50))
    code_block1 = draw(st.integers(min_value=0, max_value=50))
    install1 = draw(st.booleans())
    usage1 = draw(st.booleans())

    # Non-negative deltas so every numeric dimension of m2 dominates m1.
    length2 = length1 + draw(st.integers(min_value=0, max_value=100_000))
    heading2 = heading1 + draw(st.integers(min_value=0, max_value=50))
    code_block2 = code_block1 + draw(st.integers(min_value=0, max_value=50))
    # Boolean domination: m2's flag is set whenever m1's is (True >= False).
    install2 = install1 or draw(st.booleans())
    usage2 = usage1 or draw(st.booleans())

    m1 = ReadmeMetrics(
        length=length1,
        heading_count=heading1,
        code_block_count=code_block1,
        has_install_section=install1,
        has_usage_section=usage1,
    )
    m2 = ReadmeMetrics(
        length=length2,
        heading_count=heading2,
        code_block_count=code_block2,
        has_install_section=install2,
        has_usage_section=usage2,
    )
    return m1, m2


@st.composite
def _dominating_signals_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Build two RepositorySignals identical except ``readme_metrics``.

    Both have ``readme_available=True`` so README_Quality is actually computed
    from the metrics (rather than short-circuiting to the missing-data 0). Every
    other field is shared verbatim between the two signals, so ONLY the README
    completeness differs and the README_Quality comparison isolates Req 2.1.
    """
    m1, m2 = draw(_dominating_readme_metrics_pair())

    # A single arbitrary "context" for all non-README fields, reused verbatim by
    # both signals. The exact values are irrelevant to README_Quality; sharing
    # them guarantees the only difference between the two signals is the metrics.
    readme_text = draw(st.text(max_size=200))
    doc_files = tuple(draw(_ANY_PATHS))
    test_files = tuple(draw(_ANY_PATHS))
    ci_files = tuple(draw(_ANY_PATHS))
    lockfiles = tuple(draw(_ANY_PATHS))
    languages = draw(
        st.dictionaries(
            keys=st.sampled_from(sorted(KNOWN_LANGUAGES)),
            values=st.integers(min_value=1, max_value=100),
            max_size=6,
        )
    )
    key_files = tuple(draw(_ANY_PATHS))
    total = draw(st.integers(min_value=0, max_value=500))
    unpinned = draw(st.integers(min_value=0, max_value=total + 50))
    consistency_ratio = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    documentation_available = draw(st.booleans())
    test_coverage_available = draw(st.booleans())
    dependency_available = draw(st.booleans())
    consistency_available = draw(st.booleans())

    def _build(metrics: ReadmeMetrics) -> RepositorySignals:
        return RepositorySignals(
            readme_text=readme_text,
            readme_metrics=metrics,
            doc_files=doc_files,
            test_files=test_files,
            ci_files=ci_files,
            lockfiles=lockfiles,
            languages=languages,
            key_files=key_files,
            dependency_total=total,
            dependency_unpinned=unpinned,
            consistency_ratio=consistency_ratio,
            readme_available=True,
            documentation_available=documentation_available,
            test_coverage_available=test_coverage_available,
            dependency_available=dependency_available,
            consistency_available=consistency_available,
        )

    return _build(m1), _build(m2)


# Feature: repo-health-score, Property 2: README quality is monotonic in completeness
@given(pair=_dominating_signals_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_2_readme_quality_is_monotonic_in_completeness(pair, weights):
    """For any pair of ReadmeMetrics where the second dominates the first in
    every completeness dimension (length, heading count, code-block count,
    install section, usage section), the README_Quality score for the second is
    greater than or equal to that for the first.

    **Validates: Requirements 2.1**
    """
    signals1, signals2 = pair

    # Sanity-check the generator: m2 dominates m1 in every dimension.
    m1 = signals1.readme_metrics
    m2 = signals2.readme_metrics
    assert m2.length >= m1.length
    assert m2.heading_count >= m1.heading_count
    assert m2.code_block_count >= m1.code_block_count
    assert m2.has_install_section >= m1.has_install_section
    assert m2.has_usage_section >= m1.has_usage_section

    score1 = _readme_quality_score(compute_health_score(signals1, weights))
    score2 = _readme_quality_score(compute_health_score(signals2, weights))

    assert score2 >= score1, (
        f"README_Quality is not monotonic: dominating metrics {m2!r} scored "
        f"{score2} < base metrics {m1!r} score {score1}"
    )

# --------------------------------------------------------------------------- #
# Property 3: Documentation score rewards documentation presence
# --------------------------------------------------------------------------- #
#
# The Documentation category depends ONLY on ``documentation_available`` and
# ``doc_files`` (see ``app.health.scoring._score_documentation``). To isolate
# the "presence rewards" guarantee we build a single arbitrary base
# RepositorySignals, then derive TWO variants that are IDENTICAL in every field
# except the documentation inputs:
#   * with-docs : ``doc_files`` non-empty AND ``documentation_available=True``
#   * no-docs   : ``doc_files=()``       AND ``documentation_available=False``
# The property asserts the Documentation score for the with-docs variant is
# greater than or equal to that for the no-docs variant (Req 2.2).


def _documentation_score(result: HealthScoreResult) -> int:
    """Extract the Documentation CategoryScore value from a HealthScoreResult."""
    for category_score in result.categories:
        if category_score.category is Category.DOCUMENTATION:
            return category_score.score
    raise AssertionError("result did not contain a Documentation category score")


@st.composite
def _documentation_presence_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Build two RepositorySignals identical except their documentation inputs.

    ``base`` supplies an arbitrary context for every non-documentation field
    (drawn once and reused verbatim so those fields cannot differ between the
    two variants). ``with_docs`` has a non-empty ``doc_files`` and
    ``documentation_available=True``; ``no_docs`` has ``doc_files=()`` and
    ``documentation_available=False``. Every other field is shared, so ONLY the
    documentation presence differs and the Documentation comparison isolates
    Req 2.2.
    """
    base = draw(_repository_signals())

    # A non-empty documentation file tuple for the with-docs variant. Drawing
    # from _ANY_PATHS with min_size>=1 guarantees at least one doc file.
    doc_files = tuple(
        draw(
            st.lists(
                st.text(alphabet=_NAME_ALPHABET + "/._-", min_size=1, max_size=20).filter(
                    lambda s: s.strip("/") != ""
                ),
                min_size=1,
                max_size=6,
                unique=True,
            )
        )
    )

    def _build(doc_files_value: tuple[str, ...], available: bool) -> RepositorySignals:
        return RepositorySignals(
            readme_text=base.readme_text,
            readme_metrics=base.readme_metrics,
            doc_files=doc_files_value,
            test_files=base.test_files,
            ci_files=base.ci_files,
            lockfiles=base.lockfiles,
            languages=base.languages,
            key_files=base.key_files,
            dependency_total=base.dependency_total,
            dependency_unpinned=base.dependency_unpinned,
            consistency_ratio=base.consistency_ratio,
            readme_available=base.readme_available,
            documentation_available=available,
            test_coverage_available=base.test_coverage_available,
            dependency_available=base.dependency_available,
            consistency_available=base.consistency_available,
        )

    with_docs = _build(doc_files, True)
    no_docs = _build((), False)
    return with_docs, no_docs


# Feature: repo-health-score, Property 3: Documentation score rewards documentation presence
@given(pair=_documentation_presence_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_3_documentation_score_rewards_presence(pair, weights):
    """For any RepositorySignals, the Documentation score with documentation
    files present (and ``documentation_available=True``) is greater than or
    equal to the score for otherwise-identical signals with no documentation
    files (``doc_files=()`` and ``documentation_available=False``).

    **Validates: Requirements 2.2**
    """
    with_docs, no_docs = pair

    # Sanity-check the generator: the two signals differ ONLY in their
    # documentation inputs; every other field is shared verbatim.
    assert with_docs.doc_files != ()
    assert no_docs.doc_files == ()
    assert with_docs.documentation_available is True
    assert no_docs.documentation_available is False
    assert with_docs.readme_text == no_docs.readme_text
    assert with_docs.readme_metrics == no_docs.readme_metrics
    assert with_docs.test_files == no_docs.test_files
    assert with_docs.ci_files == no_docs.ci_files
    assert with_docs.lockfiles == no_docs.lockfiles
    assert with_docs.languages == no_docs.languages
    assert with_docs.key_files == no_docs.key_files
    assert with_docs.dependency_total == no_docs.dependency_total
    assert with_docs.dependency_unpinned == no_docs.dependency_unpinned
    assert with_docs.consistency_ratio == no_docs.consistency_ratio
    assert with_docs.readme_available == no_docs.readme_available
    assert with_docs.test_coverage_available == no_docs.test_coverage_available
    assert with_docs.dependency_available == no_docs.dependency_available
    assert with_docs.consistency_available == no_docs.consistency_available

    score_with_docs = _documentation_score(compute_health_score(with_docs, weights))
    score_no_docs = _documentation_score(compute_health_score(no_docs, weights))

    assert score_with_docs >= score_no_docs, (
        f"Documentation score does not reward presence: with docs "
        f"{with_docs.doc_files!r} scored {score_with_docs} < no-docs score "
        f"{score_no_docs}"
    )

# --------------------------------------------------------------------------- #
# Property 4: Test coverage score rewards test files and CI
# --------------------------------------------------------------------------- #
#
# The Test_Coverage category depends ONLY on ``test_coverage_available``,
# ``test_files`` and ``ci_files`` (see ``app.health.scoring._score_test_coverage``).
# To isolate the "adding test files and/or CI never decreases the score"
# guarantee we build a single arbitrary base RepositorySignals, then derive TWO
# variants that are IDENTICAL in every field except the test/CI inputs:
#   * base      : an arbitrary NON-EMPTY set of test files and an arbitrary CI
#                 presence, with ``test_coverage_available=True``.
#   * augmented : a SUPERSET of the base's test files (adds zero or more new
#                 test files) and CI that is turned ON whenever the base had it
#                 (never turned off), also ``test_coverage_available=True``.
# Both variants keep ``test_coverage_available=True`` so neither short-circuits
# to the missing-data 0 (which would let the availability flag, not the
# test/CI presence, drive the comparison). The property asserts the augmented
# variant's Test_Coverage score is greater than or equal to the base's (Req 2.3).


def _test_coverage_score(result: HealthScoreResult) -> int:
    """Extract the Test_Coverage CategoryScore value from a HealthScoreResult."""
    for category_score in result.categories:
        if category_score.category is Category.TEST_COVERAGE:
            return category_score.score
    raise AssertionError("result did not contain a Test_Coverage category score")


# A pool of distinct, plausible file paths from which the base and the ADDED
# test files are drawn. Uniqueness across the whole pool guarantees the
# augmented variant's test files are a genuine superset of the base's (added
# paths are drawn disjoint from the base's paths).
_TEST_FILE_POOL = st.text(
    alphabet=_NAME_ALPHABET + "/._-", min_size=1, max_size=20
).filter(lambda s: s.strip("/") != "")


@st.composite
def _test_coverage_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Build two RepositorySignals identical except their test/CI inputs.

    ``base`` supplies an arbitrary context for every non-test/CI field (drawn
    once and reused verbatim so those fields cannot differ between the two
    variants). Both variants set ``test_coverage_available=True`` so the score
    is actually computed from the test/CI presence rather than short-circuiting
    to the missing-data 0.

    The augmented variant's test files are a SUPERSET of the base's (zero or
    more additional, disjoint test files) and its CI presence is the base's CI
    OR-ed with an extra boolean, so CI is only ever added, never removed. This
    makes ``augmented`` differ from ``base`` only by ADDING test files and/or
    ADDING CI — exactly the transformation Property 4 quantifies over.
    """
    base = draw(_repository_signals())

    # A pool of unique test file paths, partitioned into the base's files and
    # the ADDED files so the augmented set is a genuine superset of the base.
    pool = draw(
        st.lists(_TEST_FILE_POOL, min_size=1, max_size=8, unique=True)
    )
    split = draw(st.integers(min_value=1, max_value=len(pool)))
    base_tests = tuple(pool[:split])
    added_tests = tuple(pool[split:])  # possibly empty
    augmented_tests = base_tests + added_tests

    base_has_ci = draw(st.booleans())
    # CI is only ever turned ON in the augmented variant (never off).
    augmented_has_ci = base_has_ci or draw(st.booleans())

    # A CI file tuple; its exact contents don't matter (only presence does).
    ci_file = draw(_TEST_FILE_POOL)
    base_ci = (ci_file,) if base_has_ci else ()
    augmented_ci = (ci_file,) if augmented_has_ci else ()

    def _build(
        test_files: tuple[str, ...], ci_files: tuple[str, ...]
    ) -> RepositorySignals:
        return RepositorySignals(
            readme_text=base.readme_text,
            readme_metrics=base.readme_metrics,
            doc_files=base.doc_files,
            test_files=test_files,
            ci_files=ci_files,
            lockfiles=base.lockfiles,
            languages=base.languages,
            key_files=base.key_files,
            dependency_total=base.dependency_total,
            dependency_unpinned=base.dependency_unpinned,
            consistency_ratio=base.consistency_ratio,
            readme_available=base.readme_available,
            documentation_available=base.documentation_available,
            test_coverage_available=True,
            dependency_available=base.dependency_available,
            consistency_available=base.consistency_available,
        )

    base_signals = _build(base_tests, base_ci)
    augmented_signals = _build(augmented_tests, augmented_ci)
    return base_signals, augmented_signals


# Feature: repo-health-score, Property 4: Test coverage score rewards test files and CI
@given(pair=_test_coverage_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_4_test_coverage_score_rewards_tests_and_ci(pair, weights):
    """For any RepositorySignals, adding test files and/or CI_Config files never
    decreases the Test_Coverage score.

    The augmented variant's test files are a superset of the base's and its CI
    is turned on whenever the base's was (never off), with every other field
    (and ``test_coverage_available=True``) shared verbatim. The Test_Coverage
    score for the augmented variant must therefore be greater than or equal to
    the base's.

    **Validates: Requirements 2.3**
    """
    base, augmented = pair

    # Sanity-check the generator: the two signals differ ONLY in their test/CI
    # inputs, the augmented test files are a SUPERSET of the base's, CI is only
    # ever added, and both keep test_coverage_available=True.
    assert base.test_coverage_available is True
    assert augmented.test_coverage_available is True
    assert set(base.test_files).issubset(set(augmented.test_files))
    # CI is only ever added: if the base has CI the augmented does too.
    assert not (bool(base.ci_files) and not bool(augmented.ci_files))
    assert base.readme_text == augmented.readme_text
    assert base.readme_metrics == augmented.readme_metrics
    assert base.doc_files == augmented.doc_files
    assert base.lockfiles == augmented.lockfiles
    assert base.languages == augmented.languages
    assert base.key_files == augmented.key_files
    assert base.dependency_total == augmented.dependency_total
    assert base.dependency_unpinned == augmented.dependency_unpinned
    assert base.consistency_ratio == augmented.consistency_ratio
    assert base.readme_available == augmented.readme_available
    assert base.documentation_available == augmented.documentation_available
    assert base.dependency_available == augmented.dependency_available
    assert base.consistency_available == augmented.consistency_available

    score_base = _test_coverage_score(compute_health_score(base, weights))
    score_augmented = _test_coverage_score(compute_health_score(augmented, weights))

    assert score_augmented >= score_base, (
        f"Test_Coverage score decreased when adding test files/CI: base "
        f"(tests={base.test_files!r}, ci={base.ci_files!r}) scored {score_base} "
        f"> augmented (tests={augmented.test_files!r}, ci={augmented.ci_files!r}) "
        f"score {score_augmented}"
    )

# --------------------------------------------------------------------------- #
# Property 5: Dependency freshness decreases with unpinned dependencies
# --------------------------------------------------------------------------- #


def _dependency_freshness_score(result: HealthScoreResult) -> int:
    """Extract the Dependency_Freshness CategoryScore value from a result."""

    for category_score in result.categories:
        if category_score.category is Category.DEPENDENCY_FRESHNESS:
            return category_score.score
    raise AssertionError("Dependency_Freshness category missing from result")


@st.composite
def _fixed_total_unpinned_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Two signals identical except for ``dependency_unpinned``.

    Both share a fixed total ``T >= 1``, ``dependency_available=True`` (a
    lockfile present) and every other field verbatim. Their unpinned counts are
    ``u1 <= u2`` (both in ``[0, T]``), so the ONLY difference is the count of
    Unpinned_Dependency instances rising from ``u1`` to ``u2`` — exactly the
    transformation Property 5's non-increasing claim quantifies over.
    """
    base = draw(_repository_signals())

    total = draw(st.integers(min_value=1, max_value=500))
    u1 = draw(st.integers(min_value=0, max_value=total))
    u2 = draw(st.integers(min_value=u1, max_value=total))

    # A non-empty lockfile tuple so the with-lockfile state is coherent; exact
    # contents are irrelevant (only presence and the counts drive the score).
    lockfiles = base.lockfiles if base.lockfiles else ("requirements.txt",)

    def _build(unpinned: int) -> RepositorySignals:
        return RepositorySignals(
            readme_text=base.readme_text,
            readme_metrics=base.readme_metrics,
            doc_files=base.doc_files,
            test_files=base.test_files,
            ci_files=base.ci_files,
            lockfiles=lockfiles,
            languages=base.languages,
            key_files=base.key_files,
            dependency_total=total,
            dependency_unpinned=unpinned,
            consistency_ratio=base.consistency_ratio,
            readme_available=base.readme_available,
            documentation_available=base.documentation_available,
            test_coverage_available=base.test_coverage_available,
            dependency_available=True,
            consistency_available=base.consistency_available,
        )

    return _build(u1), _build(u2)


# Feature: repo-health-score, Property 5: Dependency freshness decreases with unpinned dependencies
@given(pair=_fixed_total_unpinned_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_5_non_increasing_in_unpinned_for_fixed_total(pair, weights):
    """For any fixed total dependency count, the Dependency_Freshness score is
    non-increasing as the count of Unpinned_Dependency instances rises.

    ``low`` and ``high`` share the same total ``T >= 1``, a present lockfile
    (``dependency_available=True``) and every other field verbatim; only
    ``dependency_unpinned`` differs, with ``low.unpinned <= high.unpinned``. The
    Dependency_Freshness score for ``high`` must therefore be less than or equal
    to the score for ``low``.

    **Validates: Requirements 2.4**
    """
    low, high = pair

    # Sanity-check the generator: the two signals differ ONLY in unpinned count,
    # share a fixed positive total, and both have a lockfile present.
    assert low.dependency_available is True
    assert high.dependency_available is True
    assert low.dependency_total == high.dependency_total
    assert low.dependency_total >= 1
    assert low.dependency_unpinned <= high.dependency_unpinned
    assert bool(low.lockfiles) and bool(high.lockfiles)
    assert low.readme_text == high.readme_text
    assert low.readme_metrics == high.readme_metrics
    assert low.doc_files == high.doc_files
    assert low.test_files == high.test_files
    assert low.ci_files == high.ci_files
    assert low.lockfiles == high.lockfiles
    assert low.languages == high.languages
    assert low.key_files == high.key_files
    assert low.consistency_ratio == high.consistency_ratio
    assert low.readme_available == high.readme_available
    assert low.documentation_available == high.documentation_available
    assert low.test_coverage_available == high.test_coverage_available
    assert low.consistency_available == high.consistency_available

    score_low = _dependency_freshness_score(compute_health_score(low, weights))
    score_high = _dependency_freshness_score(compute_health_score(high, weights))

    assert score_high <= score_low, (
        f"Dependency_Freshness score increased as unpinned rose for a fixed "
        f"total {low.dependency_total}: unpinned={low.dependency_unpinned} scored "
        f"{score_low} but unpinned={high.dependency_unpinned} scored {score_high}"
    )


@st.composite
def _lockfile_presence_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Two signals identical except for lockfile presence.

    ``present`` has ``dependency_available=True`` (a lockfile present) and
    ``absent`` has ``dependency_available=False`` (no lockfile). Every other
    field is shared verbatim, so the ONLY difference is whether a lockfile is
    present — exactly the transformation Property 5's lockfile claim quantifies
    over.
    """
    base = draw(_repository_signals())

    total = draw(st.integers(min_value=0, max_value=500))
    unpinned = draw(st.integers(min_value=0, max_value=total))
    lockfiles = base.lockfiles if base.lockfiles else ("requirements.txt",)

    def _build(available: bool) -> RepositorySignals:
        return RepositorySignals(
            readme_text=base.readme_text,
            readme_metrics=base.readme_metrics,
            doc_files=base.doc_files,
            test_files=base.test_files,
            ci_files=base.ci_files,
            lockfiles=lockfiles,
            languages=base.languages,
            key_files=base.key_files,
            dependency_total=total,
            dependency_unpinned=unpinned,
            consistency_ratio=base.consistency_ratio,
            readme_available=base.readme_available,
            documentation_available=base.documentation_available,
            test_coverage_available=base.test_coverage_available,
            dependency_available=available,
            consistency_available=base.consistency_available,
        )

    return _build(True), _build(False)


# Feature: repo-health-score, Property 5: Dependency freshness decreases with unpinned dependencies
@given(pair=_lockfile_presence_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_5_lockfile_presence_never_lowers_score(pair, weights):
    """Lockfile presence never lowers the Dependency_Freshness score relative to
    no lockfile.

    ``present`` and ``absent`` share every field verbatim except
    ``dependency_available``: ``present`` has a lockfile, ``absent`` does not
    (which yields a missing-data score of 0). The Dependency_Freshness score for
    ``present`` must therefore be greater than or equal to the score for
    ``absent`` (which is 0).

    **Validates: Requirements 2.4**
    """
    present, absent = pair

    # Sanity-check the generator: the two signals differ ONLY in lockfile
    # presence; every other field is shared verbatim.
    assert present.dependency_available is True
    assert absent.dependency_available is False
    assert present.dependency_total == absent.dependency_total
    assert present.dependency_unpinned == absent.dependency_unpinned
    assert present.lockfiles == absent.lockfiles
    assert present.readme_text == absent.readme_text
    assert present.readme_metrics == absent.readme_metrics
    assert present.doc_files == absent.doc_files
    assert present.test_files == absent.test_files
    assert present.ci_files == absent.ci_files
    assert present.languages == absent.languages
    assert present.key_files == absent.key_files
    assert present.consistency_ratio == absent.consistency_ratio
    assert present.readme_available == absent.readme_available
    assert present.documentation_available == absent.documentation_available
    assert present.test_coverage_available == absent.test_coverage_available
    assert present.consistency_available == absent.consistency_available

    score_present = _dependency_freshness_score(
        compute_health_score(present, weights)
    )
    score_absent = _dependency_freshness_score(compute_health_score(absent, weights))

    # No lockfile is treated as missing data -> score 0 (Req 2.6).
    assert score_absent == 0
    assert score_present >= score_absent, (
        f"lockfile presence lowered the Dependency_Freshness score: present "
        f"scored {score_present} < absent scored {score_absent}"
    )


# --------------------------------------------------------------------------- #
# Property 6: Consistency score is monotonic in match ratio
# --------------------------------------------------------------------------- #


def _consistency_score(result: HealthScoreResult) -> int:
    """Extract the Readme_Code_Consistency CategoryScore value from a result."""

    for category_score in result.categories:
        if category_score.category is Category.README_CODE_CONSISTENCY:
            return category_score.score
    raise AssertionError("Readme_Code_Consistency category missing from result")


@st.composite
def _ordered_consistency_ratio_pair(draw) -> tuple[RepositorySignals, RepositorySignals]:
    """Two signals identical except for ``consistency_ratio``.

    Both share ``consistency_available=True`` and every other field verbatim.
    Their ratios are ``r1 <= r2`` (both in ``[0.0, 1.0]``), so the ONLY
    difference is the README-vs-code match ratio rising from ``r1`` to ``r2`` —
    exactly the transformation Property 6's monotonicity claim quantifies over.
    """
    base = draw(_repository_signals())

    r1 = draw(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    r2 = draw(
        st.floats(min_value=r1, max_value=1.0, allow_nan=False, allow_infinity=False)
    )

    def _build(ratio: float) -> RepositorySignals:
        return RepositorySignals(
            readme_text=base.readme_text,
            readme_metrics=base.readme_metrics,
            doc_files=base.doc_files,
            test_files=base.test_files,
            ci_files=base.ci_files,
            lockfiles=base.lockfiles,
            languages=base.languages,
            key_files=base.key_files,
            dependency_total=base.dependency_total,
            dependency_unpinned=base.dependency_unpinned,
            consistency_ratio=ratio,
            readme_available=base.readme_available,
            documentation_available=base.documentation_available,
            test_coverage_available=base.test_coverage_available,
            dependency_available=base.dependency_available,
            consistency_available=True,
        )

    return _build(r1), _build(r2)


# Feature: repo-health-score, Property 6: Consistency score is monotonic in match ratio
@given(pair=_ordered_consistency_ratio_pair(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_6_consistency_score_is_monotonic_in_match_ratio(pair, weights):
    """For any two consistency ratios ``r1 <= r2``, the Readme_Code_Consistency
    score for ``r1`` is less than or equal to the score for ``r2``.

    ``low`` and ``high`` share ``consistency_available=True`` and every other
    field verbatim; only ``consistency_ratio`` differs, with
    ``low.consistency_ratio <= high.consistency_ratio``. The
    Readme_Code_Consistency score for ``high`` must therefore be greater than or
    equal to the score for ``low``.

    **Validates: Requirements 2.5**
    """
    low, high = pair

    # Sanity-check the generator: the two signals differ ONLY in consistency
    # ratio (ordered r1 <= r2), share consistency availability, and every other
    # field is verbatim.
    assert low.consistency_available is True
    assert high.consistency_available is True
    assert 0.0 <= low.consistency_ratio <= high.consistency_ratio <= 1.0
    assert low.readme_text == high.readme_text
    assert low.readme_metrics == high.readme_metrics
    assert low.doc_files == high.doc_files
    assert low.test_files == high.test_files
    assert low.ci_files == high.ci_files
    assert low.lockfiles == high.lockfiles
    assert low.languages == high.languages
    assert low.key_files == high.key_files
    assert low.dependency_total == high.dependency_total
    assert low.dependency_unpinned == high.dependency_unpinned
    assert low.readme_available == high.readme_available
    assert low.documentation_available == high.documentation_available
    assert low.test_coverage_available == high.test_coverage_available
    assert low.dependency_available == high.dependency_available

    score_low = _consistency_score(compute_health_score(low, weights))
    score_high = _consistency_score(compute_health_score(high, weights))

    assert score_high >= score_low, (
        f"Readme_Code_Consistency score decreased as the match ratio rose: "
        f"ratio={low.consistency_ratio} scored {score_low} but "
        f"ratio={high.consistency_ratio} scored {score_high}"
    )

# --------------------------------------------------------------------------- #
# Property 7: Unavailable signal defaults to zero with missing_data
# --------------------------------------------------------------------------- #
#
# Each of the five categories is scored from exactly one availability flag on
# the RepositorySignals. When that flag is False the underlying signal is
# unavailable, so the category MUST score 0, carry ``missing_data=True``, and
# STILL be present in the HealthScoreResult (i.e. it is included in the
# composite rather than dropped). This maps each Category to its flag:
_CATEGORY_TO_AVAILABILITY: dict[Category, str] = {
    Category.README_QUALITY: "readme_available",
    Category.DOCUMENTATION: "documentation_available",
    Category.TEST_COVERAGE: "test_coverage_available",
    Category.DEPENDENCY_FRESHNESS: "dependency_available",
    Category.README_CODE_CONSISTENCY: "consistency_available",
}


@st.composite
def _repository_signals_with_forced_unavailability(draw) -> RepositorySignals:
    """An arbitrary RepositorySignals with a random NON-EMPTY subset of the five
    availability flags forced to ``False``.

    ``_repository_signals`` already draws each flag independently, so the
    unavailable case arises naturally; this variant additionally guarantees at
    least one flag is unavailable on every example, so the "unavailable ->
    score 0, missing_data True, still present" branch is exercised on every run
    (not merely often). All other fields remain arbitrary.
    """
    base = draw(_repository_signals())

    flags = [
        "readme_available",
        "documentation_available",
        "test_coverage_available",
        "dependency_available",
        "consistency_available",
    ]
    # A non-empty subset of flags to force False (at least one guaranteed).
    forced_false = draw(
        st.lists(st.sampled_from(flags), min_size=1, max_size=5, unique=True)
    )

    from dataclasses import replace

    return replace(base, **{flag: False for flag in forced_false})


# Feature: repo-health-score, Property 7: Unavailable signal defaults to zero with missing_data
@given(
    signals=st.one_of(
        _repository_signals(),
        _repository_signals_with_forced_unavailability(),
    ),
    weights=_WEIGHTS_ARG,
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_7_unavailable_signal_defaults_to_zero_with_missing_data(
    signals, weights
):
    """For any RepositorySignals (with arbitrary availability flags), every one
    of the five categories is present in the HealthScoreResult, and for each
    category whose underlying availability flag is False the CategoryScore is 0,
    its ``missing_data`` indicator is True, and it is still included in the
    composite (i.e. present among the five categories).

    **Validates: Requirements 2.6**
    """
    result = compute_health_score(signals, weights)

    # All five categories are ALWAYS present (included in the composite),
    # each exactly once — an unavailable signal must never drop a category.
    assert len(result.categories) == 5
    by_category = {cs.category: cs for cs in result.categories}
    assert set(by_category) == set(Category)

    for category, flag_name in _CATEGORY_TO_AVAILABILITY.items():
        category_score = by_category[category]
        available = getattr(signals, flag_name)
        if not available:
            assert category_score.score == 0, (
                f"category {category.value!r} has unavailable signal "
                f"({flag_name}=False) but score is {category_score.score}, not 0"
            )
            assert category_score.missing_data is True, (
                f"category {category.value!r} has unavailable signal "
                f"({flag_name}=False) but missing_data is not True"
            )
            # Presence in the composite is guaranteed by the membership check
            # above; assert it explicitly for this category for clarity.
            assert category in by_category, (
                f"category {category.value!r} was dropped from the result "
                f"despite its signal being unavailable"
            )


# --------------------------------------------------------------------------- #
# Property 8: Scoring is deterministic and pure
# --------------------------------------------------------------------------- #
#
# Determinism/purity is exercised over the FULL signal space (arbitrary
# ``_repository_signals``) and the full weights domain (``_WEIGHTS_ARG``: either
# ``None`` -> configured defaults, or an arbitrary valid config). We compute the
# score TWICE with the same inputs and require the two ``HealthScoreResult``
# values to be identical (frozen dataclasses compare by value, so ``==`` checks
# the composite AND the categories tuple element-by-element). Purity is also
# asserted directly: the input ``RepositorySignals`` is unchanged by the call
# (it equals a snapshot taken before scoring; frozen dataclasses cannot mutate,
# so this guards against any accidental rebinding of shared state).


# Feature: repo-health-score, Property 8: Scoring is deterministic and pure
@given(signals=_repository_signals(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_8_scoring_is_deterministic_and_pure(signals, weights):
    """For any RepositorySignals and any valid Weight_Config, computing the
    health score twice with the same inputs produces identical
    HealthScoreResult values (equal composite and equal categories tuple), and
    the input signals object is left unchanged by the computation.

    **Validates: Requirements 2.7, 5.4**
    """
    # A snapshot of the input BEFORE scoring, to assert purity (the scorer must
    # not mutate or otherwise alter the signals it is given).
    signals_before = dataclasses.replace(signals)

    first = compute_health_score(signals, weights)
    second = compute_health_score(signals, weights)

    # Determinism: the two results are equal. On the frozen dataclass, ``==``
    # compares the composite and the categories tuple structurally, so this one
    # assertion covers "composite equal AND categories tuple equal".
    assert first == second, (
        f"scoring was not deterministic: {first!r} != {second!r}"
    )

    # Make the composite / categories equality explicit as well.
    assert first.composite == second.composite
    assert first.categories == second.categories

    # Purity: the input signals object is unchanged after the call (equal to the
    # pre-call snapshot). A frozen dataclass cannot be mutated in place, so this
    # confirms the scorer neither rebinds nor corrupts the input it received.
    assert signals == signals_before

# --------------------------------------------------------------------------- #
# Property 20: Composite is the round-half-up weighted sum in range
# --------------------------------------------------------------------------- #
#
# The composite must equal the weighted sum of the five category scores using
# weights normalized to sum 1.0, rounded to the nearest integer with halves
# rounded UP (``math.floor(x + 0.5)``), and must lie in ``[0, 100]``. We
# recompute the expected composite INDEPENDENTLY of ``compute_health_score``:
# we take the five category scores it produced, re-normalize the SAME weights it
# used (the configured defaults when ``weights is None``, otherwise the supplied
# config), form the weighted sum in the canonical category order, and apply the
# round-half-up rule ourselves. Reusing the emitted category scores isolates the
# aggregation-and-rounding guarantee (Req 5.1/5.2) from the per-category scoring
# already covered by Properties 1-7.

from app.health.weights import load_weights  # noqa: E402


# Feature: repo-health-score, Property 20: Composite is the round-half-up weighted sum in range
@given(signals=_repository_signals(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_20_composite_is_round_half_up_weighted_sum_in_range(signals, weights):
    """For any RepositorySignals and any valid Weight_Config (or ``None`` for the
    defaults), the composite equals the weighted sum of the five category scores
    using weights normalized to sum 1.0, rounded to the nearest integer with
    halves rounded up, and lies in the range 0 to 100 inclusive.

    **Validates: Requirements 5.1, 5.2**
    """
    result = compute_health_score(signals, weights)

    # The exact weights the scorer used: defaults when None, else the supplied
    # config. Normalize the SAME way the scorer does (sum to 1.0).
    active_weights = load_weights() if weights is None else weights
    normalized = normalize_weights(active_weights)

    # Extract each category's integer score from the emitted result, keyed by
    # category, so the independent recompute uses the scorer's own per-category
    # scores (isolating the aggregation/rounding guarantee).
    scores_by_category = {cs.category: cs.score for cs in result.categories}
    assert set(scores_by_category) == set(Category), (
        "result did not contain all five categories"
    )

    # Independent weighted sum in the canonical category order, then round half
    # up via math.floor(x + 0.5) — matching Req 5.2 (guards banker's rounding).
    weighted_sum = sum(
        normalized[category] * scores_by_category[category] for category in Category
    )
    expected_composite = math.floor(weighted_sum + 0.5)

    assert result.composite == expected_composite, (
        f"composite {result.composite} != round-half-up weighted sum "
        f"{expected_composite} (weighted_sum={weighted_sum!r}, "
        f"scores={ {c.value: s for c, s in scores_by_category.items()} })"
    )
    # The composite lies in the inclusive range 0-100 (Req 5.2).
    assert 0 <= result.composite <= 100, (
        f"composite {result.composite} is outside the range [0, 100]"
    )

# --------------------------------------------------------------------------- #
# Property 21: Result contains the composite and all five categories
# --------------------------------------------------------------------------- #


# Feature: repo-health-score, Property 21: Result contains the composite and all five categories
@given(signals=_repository_signals(), weights=_WEIGHTS_ARG)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_21_result_contains_composite_and_all_five_categories(signals, weights):
    """For any RepositorySignals and valid Weight_Config, the HealthScoreResult
    contains a composite score and exactly one CategoryScore for each of the
    five defined categories.

    **Validates: Requirements 5.3**
    """
    result = compute_health_score(signals, weights)

    # The composite is an int in the inclusive range 0-100. A bool is an int
    # subclass, so reject it explicitly to require a genuine int.
    assert isinstance(result.composite, int) and not isinstance(result.composite, bool), (
        f"composite is not an int: {result.composite!r}"
    )
    assert 0 <= result.composite <= 100, (
        f"composite {result.composite} is outside the range [0, 100]"
    )

    # Exactly five category scores are returned.
    assert len(result.categories) == 5, (
        f"expected exactly 5 category scores, got {len(result.categories)}"
    )

    categories = [cs.category for cs in result.categories]

    # The set of categories is precisely the five defined categories.
    assert set(categories) == set(Category), (
        f"categories {set(categories)} != the five defined categories "
        f"{set(Category)}"
    )

    # Each category appears exactly once (no duplicates). With len == 5 and the
    # set equal to the five categories, the multiset must contain each once.
    for category in Category:
        assert categories.count(category) == 1, (
            f"category {category.value!r} appears {categories.count(category)} "
            f"times; expected exactly once"
        )

# --------------------------------------------------------------------------- #
# Property 22: Out-of-range category score raises ScoreValueError
# --------------------------------------------------------------------------- #

from unittest import mock  # noqa: E402

import app.health.scoring as scoring_module  # noqa: E402
from app.health.errors import ScoreValueError  # noqa: E402

# Map each category to the private per-category scoring helper in
# ``app.health.scoring`` that ``compute_health_score`` calls to produce that
# category's ``(score, missing_data)`` tuple. Sabotaging one helper to return an
# out-of-range score is the only way to drive the (otherwise unreachable) 0-100
# guard, since the real helpers always return in-range scores.
_CATEGORY_TO_HELPER = {
    Category.README_QUALITY: "_score_readme_quality",
    Category.DOCUMENTATION: "_score_documentation",
    Category.TEST_COVERAGE: "_score_test_coverage",
    Category.DEPENDENCY_FRESHNESS: "_score_dependency_freshness",
    Category.README_CODE_CONSISTENCY: "_score_consistency",
}

# An integer strictly outside the valid 0-100 range: either below 0 or above
# 100. Bounds are kept finite and modest; the guard only cares about the
# ``< 0 or > 100`` predicate, not the magnitude.
_OUT_OF_RANGE_SCORE = st.one_of(
    st.integers(min_value=-10_000, max_value=-1),  # below the range
    st.integers(min_value=101, max_value=10_000),  # above the range
)


# Feature: repo-health-score, Property 22: Out-of-range category score raises ScoreValueError
@given(
    signals=_repository_signals(),
    weights=_WEIGHTS_ARG,
    sabotaged=st.sampled_from(tuple(Category)),
    bad_score=_OUT_OF_RANGE_SCORE,
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_22_out_of_range_category_score_raises(
    signals, weights, sabotaged, bad_score
):
    """For any category score forced outside the range 0 to 100,
    ``compute_health_score`` raises ``ScoreValueError`` identifying the offending
    category and returns no composite.

    The real per-category helpers always produce in-range scores, so the 0-100
    guard is unreachable through ordinary inputs. To force the condition we
    monkeypatch (via ``mock.patch.object``) exactly one per-category helper in
    the scoring module so it returns ``(bad_score, False)`` with ``bad_score``
    strictly outside 0-100, leaving the other four helpers untouched. The guard
    must then raise ``ScoreValueError`` for the sabotaged category before any
    ``HealthScoreResult`` is produced.

    **Validates: Requirements 5.6**
    """
    helper_name = _CATEGORY_TO_HELPER[sabotaged]

    # Sanity: the bad score really is outside the valid range.
    assert bad_score < 0 or bad_score > 100

    with mock.patch.object(
        scoring_module,
        helper_name,
        return_value=(bad_score, False),
    ):
        try:
            result = scoring_module.compute_health_score(signals, weights)
        except ScoreValueError as exc:
            # The error identifies the offending category — either directly via
            # its ``.category`` attribute or by naming the category value in its
            # message.
            assert exc.category == sabotaged, (
                f"ScoreValueError.category {exc.category!r} does not identify the "
                f"sabotaged category {sabotaged!r}"
            )
            assert sabotaged.value in str(exc), (
                f"error message does not name the offending category "
                f"{sabotaged.value!r}: {exc!s}"
            )
            # It also carried the offending out-of-range score.
            assert exc.score == bad_score
            return  # expected: the exception propagated; no composite produced

    # Reaching here means no exception was raised and a result came back — the
    # guard failed to reject the out-of-range score.
    raise AssertionError(
        f"compute_health_score returned {result!r} instead of raising "
        f"ScoreValueError for out-of-range {sabotaged.value!r} score {bad_score!r}"
    )
