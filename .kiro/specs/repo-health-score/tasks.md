# Implementation Plan: Repo Health Score

## Overview

This plan builds the Repo Health Score feature bottom-up: the pure `app/health/` core (errors → models → weights → lockfile → signals → scoring) is implemented and property-/unit-tested in isolation before any network, API, or UI code depends on it. The `code_analyzer` refactor exposes `StructuredCodeData` (with the guaranteed lockfile fetch) while keeping `get_code_summary()` byte-for-byte compatible. Finally the score is wired into `app/main.py` behind `HEALTH_SCORING_ENABLED` and rendered on the Streamlit dashboard.

The implementation language is **Python** (the design specifies Python throughout). Testing uses `pytest` + `hypothesis` as dev dependencies (none exist today). Property tests are one test per correctness property, run a minimum of 100 iterations, and are tagged with the comment format `# Feature: repo-health-score, Property {n}: {text}`.

## Tasks

- [ ] 1. Set up test tooling and the health package skeleton
  - [ ] 1.1 Add dev test dependencies and pytest configuration
    - Create `requirements-dev.txt` pinning `pytest` and `hypothesis` to exact versions; keep them out of the runtime `requirements.txt` / `requirements-backend.txt`
    - Add a minimal `pytest.ini` (or `pyproject.toml [tool.pytest.ini_options]`) configuring `testpaths = tests` and default options; document running with `pytest tests/health`
    - Create `tests/__init__.py` and `tests/health/__init__.py`
    - _Requirements: Design "Testing Strategy / Tooling"_

  - [ ] 1.2 Create the `app/health/` package skeleton and error types
    - Create `app/health/__init__.py` with package exports
    - Create `app/health/errors.py` defining `HealthScoreError`, `ScoreValueError` (names offending category), and `WeightConfigError`
    - _Requirements: 5.6, 6.5, 6.6_

- [ ] 2. Implement health data models
  - [ ] 2.1 Define frozen dataclasses and the Category enum
    - In `app/health/models.py` define `Category` enum (5 members) and frozen dataclasses `StructuredCodeData`, `ReadmeMetrics`, `DependencyPinning`, `RepositorySignals`, `CategoryScore`, `HealthScoreResult` per the design Data Models section
    - Make all health dataclasses `frozen=True` (immutable/hashable) to reinforce purity
    - _Requirements: 1.2, 2.6, 5.3_

  - [ ]* 2.2 Write unit tests for models
    - Verify frozenness/immutability and that `Category` has exactly the five defined members with expected string values
    - _Requirements: 1.2, 5.3_

- [ ] 3. Implement configuration-driven weights
  - [ ] 3.1 Implement `weights.py` (defaults, load, normalize, validation)
    - Define `DEFAULT_WEIGHTS: dict[Category, float]` with one non-negative weight per category, in `app/health/weights.py` separate from scoring
    - Implement `load_weights(overrides)` and `normalize_weights(weights)` that validate then normalize to sum 1.0, raising `WeightConfigError` for a missing category (named), a negative/non-numeric weight, or a zero sum
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 3.2 Write property test: composite invariant under positive weight scaling
    - `# Feature: repo-health-score, Property 23: Composite is invariant under positive weight scaling`
    - **Property 23** — **Validates: Requirements 6.4** (min 100 iterations)

  - [ ]* 3.3 Write property test: missing weight raises named configuration error
    - `# Feature: repo-health-score, Property 24: Missing weight raises a named configuration error`
    - **Property 24** — **Validates: Requirements 6.5** (min 100 iterations)

  - [ ]* 3.4 Write property test: invalid weight configuration raises a configuration error
    - `# Feature: repo-health-score, Property 25: Invalid weight configuration raises a configuration error`
    - **Property 25** — **Validates: Requirements 6.6** (min 100 iterations)

  - [ ]* 3.5 Write unit tests for weight defaults and normalization edges
    - Assert `DEFAULT_WEIGHTS` defines all five categories and is non-negative; test explicit non-normalized inputs normalize to sum 1.0
    - _Requirements: 6.1, 6.4_

- [ ] 4. Implement the lockfile pinning parser
  - [ ] 4.1 Implement `lockfile.py` `parse_pinning`
    - Implement format-dispatched `parse_pinning(filename, content) -> DependencyPinning` in `app/health/lockfile.py` for `requirements.txt`, `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`
    - Classify exact versions as pinned; wildcards, open/unbounded ranges, and no-specifier declarations as `Unpinned_Dependency`; return `DependencyPinning(0, 0)` for unknown/unparseable content and issue no network request
    - _Requirements: 4.1, 4.2, 4.3, 4.5_

  - [ ]* 4.2 Write property test: lockfile parse preserves declaration count
    - `# Feature: repo-health-score, Property 16: Lockfile parse preserves declaration count`
    - **Property 16** — **Validates: Requirements 4.1** (min 100 iterations)

  - [ ]* 4.3 Write property test: pinned/unpinned classification is correct
    - `# Feature: repo-health-score, Property 17: Pinned/unpinned classification is correct`
    - **Property 17** — **Validates: Requirements 4.2, 4.3** (min 100 iterations)

  - [ ]* 4.4 Write unit tests for parse_pinning edge cases
    - Unknown filename → `(0,0)`; empty/malformed content → `(0,0)`; per-format fixtures (pip specifiers, npm dep maps, go pseudo-versions)
    - _Requirements: 4.1, 4.2, 4.3_

- [ ] 5. Implement signal extraction
  - [ ] 5.1 Implement file classification and `extract_signals` core
    - Implement `extract_signals(readme, code) -> RepositorySignals` in `app/health/signals.py`
    - Classify files into documentation / test / CI_Config / lockfile groups by path pattern; populate languages, key_files, and lockfiles from structured `StructuredCodeData` (never by parsing formatted text)
    - Set per-category availability flags; when README or code data is absent, populate empty collections/strings and set the affected availability flags to false; perform no external network request
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [ ] 5.2 Implement README-vs-code consistency derivation
    - Tokenize README, match tokens case-insensitively against known language names + known key-file names, deduplicate case-insensitively, partition into matches/mismatches, and compute `consistency_ratio` in [0.0, 1.0]
    - Set `consistency_available` false when README has no detectable references or code structure has no languages and no key files
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ] 5.3 Aggregate dependency pinning across lockfiles
    - For each detected lockfile call `parse_pinning` and aggregate `dependency_total` / `dependency_unpinned`; set `dependency_available` false when no lockfile is detected; treat "present but contents unavailable" as the rare fallback (`total=0, unpinned=0`)
    - _Requirements: 4.4, 4.6_

  - [ ]* 5.4 Write property test: availability flags match data presence
    - `# Feature: repo-health-score, Property 9: Availability flags match data presence`
    - **Property 9** — **Validates: Requirements 1.6, 1.7** (min 100 iterations)

  - [ ]* 5.5 Write property test: file classification partitions by pattern
    - `# Feature: repo-health-score, Property 10: File classification partitions by pattern`
    - **Property 10** — **Validates: Requirements 1.3** (min 100 iterations)

  - [ ]* 5.6 Write property test: technology-reference recognition is case-insensitive
    - `# Feature: repo-health-score, Property 11: Technology-reference recognition is case-insensitive`
    - **Property 11** — **Validates: Requirements 3.1** (min 100 iterations)

  - [ ]* 5.7 Write property test: detectable references are deduplicated case-insensitively
    - `# Feature: repo-health-score, Property 12: Detectable references are deduplicated case-insensitively`
    - **Property 12** — **Validates: Requirements 3.2** (min 100 iterations)

  - [ ]* 5.8 Write property test: matches and mismatches partition the reference set
    - `# Feature: repo-health-score, Property 13: Matches and mismatches partition the reference set`
    - **Property 13** — **Validates: Requirements 3.3, 3.4** (min 100 iterations)

  - [ ]* 5.9 Write property test: consistency ratio is well-defined and in range
    - `# Feature: repo-health-score, Property 14: Consistency ratio is well-defined and in range`
    - **Property 14** — **Validates: Requirements 3.5** (min 100 iterations)

  - [ ]* 5.10 Write property test: consistency availability conditions
    - `# Feature: repo-health-score, Property 15: Consistency availability conditions`
    - **Property 15** — **Validates: Requirements 3.6, 3.7** (min 100 iterations)

  - [ ]* 5.11 Write property test: dependency counts aggregate across lockfiles
    - `# Feature: repo-health-score, Property 18: Dependency counts aggregate across lockfiles`
    - **Property 18** — **Validates: Requirements 4.4** (min 100 iterations)

  - [ ]* 5.12 Write property test: dependency availability requires a lockfile
    - `# Feature: repo-health-score, Property 19: Dependency availability requires a lockfile`
    - **Property 19** — **Validates: Requirements 4.6** (min 100 iterations)

  - [ ]* 5.13 Write unit tests for extract_signals (structured-not-text, empty inputs)
    - Assert extraction reads structured fields and never parses the formatted summary (Req 1.4); cover empty README, empty files, empty languages/key_files
    - _Requirements: 1.4, 1.6_

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement the pure scoring core
  - [ ] 7.1 Implement `compute_health_score`
    - Implement `compute_health_score(signals, weights=None) -> HealthScoreResult` in `app/health/scoring.py`
    - Compute the five integer CategoryScores (0-100) with the monotonic directions of Req 2; unavailable signal → score 0 with `missing_data=True`, still included in the composite
    - Normalize weights at compute time; composite = round-half-up weighted sum via `math.floor(x + 0.5)`, integer 0-100; raise `ScoreValueError` if any category score is outside 0-100
    - Restrict imports to `math`, `app.health.models`, `app.health.weights`, `app.health.errors` only
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.4_

  - [ ]* 7.2 Write property test: every category score is within range
    - `# Feature: repo-health-score, Property 1: Every category score is within range`
    - **Property 1** — **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5** (min 100 iterations)

  - [ ]* 7.3 Write property test: README quality is monotonic in completeness
    - `# Feature: repo-health-score, Property 2: README quality is monotonic in completeness`
    - **Property 2** — **Validates: Requirements 2.1** (min 100 iterations)

  - [ ]* 7.4 Write property test: documentation score rewards documentation presence
    - `# Feature: repo-health-score, Property 3: Documentation score rewards documentation presence`
    - **Property 3** — **Validates: Requirements 2.2** (min 100 iterations)

  - [ ]* 7.5 Write property test: test coverage score rewards test files and CI
    - `# Feature: repo-health-score, Property 4: Test coverage score rewards test files and CI`
    - **Property 4** — **Validates: Requirements 2.3** (min 100 iterations)

  - [ ]* 7.6 Write property test: dependency freshness decreases with unpinned dependencies
    - `# Feature: repo-health-score, Property 5: Dependency freshness decreases with unpinned dependencies`
    - **Property 5** — **Validates: Requirements 2.4** (min 100 iterations)

  - [ ]* 7.7 Write property test: consistency score is monotonic in match ratio
    - `# Feature: repo-health-score, Property 6: Consistency score is monotonic in match ratio`
    - **Property 6** — **Validates: Requirements 2.5** (min 100 iterations)

  - [ ]* 7.8 Write property test: unavailable signal defaults to zero with missing_data
    - `# Feature: repo-health-score, Property 7: Unavailable signal defaults to zero with missing_data`
    - **Property 7** — **Validates: Requirements 2.6** (min 100 iterations)

  - [ ]* 7.9 Write property test: scoring is deterministic and pure
    - `# Feature: repo-health-score, Property 8: Scoring is deterministic and pure`
    - **Property 8** — **Validates: Requirements 2.7, 5.4** (min 100 iterations)

  - [ ]* 7.10 Write property test: composite is the round-half-up weighted sum in range
    - `# Feature: repo-health-score, Property 20: Composite is the round-half-up weighted sum in range`
    - **Property 20** — **Validates: Requirements 5.1, 5.2** (min 100 iterations)

  - [ ]* 7.11 Write property test: result contains the composite and all five categories
    - `# Feature: repo-health-score, Property 21: Result contains the composite and all five categories`
    - **Property 21** — **Validates: Requirements 5.3** (min 100 iterations)

  - [ ]* 7.12 Write property test: out-of-range category score raises ScoreValueError
    - `# Feature: repo-health-score, Property 22: Out-of-range category score raises ScoreValueError`
    - **Property 22** — **Validates: Requirements 5.6** (min 100 iterations)

  - [ ]* 7.13 Write unit tests: round-half-up boundary and import boundary
    - Round-half-up boundary example (a weighted sum landing on exactly `x.5` rounds up, guarding banker's rounding)
    - Import-boundary test asserting `app/health/scoring.py` imports only `math`, `models`, `weights`, `errors`
    - _Requirements: 5.2, 5.5_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Refactor code_analyzer to expose structured data with guaranteed lockfile fetch
  - [ ] 9.1 Add `is_lockfile` predicate and structured tree retention
    - Add `is_lockfile(path)` recognizing `requirements.txt`, `package.json`, `pyproject.toml`, `setup.py`, `go.mod`, `Cargo.toml`, and similar manifests in `app/code_analyzer.py`
    - Update the tree scan so non-lockfile files are capped at `max_files` while detected lockfiles are ALWAYS retained (appended unconditionally, never dropped by the early break)
    - _Requirements: 1.2, 4.1; Design "Guaranteed Lockfile Fetch" (1)_

  - [ ] 9.2 Implement `analyze_repository` returning `StructuredCodeData`
    - Implement `analyze_repository(owner, repo, max_files=20) -> StructuredCodeData` building `files`, `languages`, `key_files`, `file_count`, and `file_contents`
    - `file_contents` MUST include the first-5-key-file samples AND every detected lockfile's content (separate, additional fetch); reuse the existing GitHub fetch flow with no registry lookups
    - "Present but contents unavailable" is the rare fallback only (genuine fetch failure/unparseable)
    - _Requirements: 1.1, 1.4, 4.1, 4.4; Design "Guaranteed Lockfile Fetch" (2), (3), (4)_

  - [ ] 9.3 Reimplement `get_code_summary` as a thin adapter
    - Make `get_code_summary(owner, repo)` call `analyze_repository()` and format its structured data into the same free-text summary, keeping the public signature and byte-for-byte output unchanged so `explain_repo_with_code` is unaffected
    - _Requirements: 1.4, 7.4_

  - [ ]* 9.4 Write unit tests for the guaranteed lockfile fetch and summary compatibility
    - Mock the GitHub fetchers: assert a lockfile beyond the `max_files` cap is retained in `files` and its content is present in `file_contents`; assert `get_code_summary` output is unchanged versus the pre-refactor format
    - _Requirements: 1.4, 4.1, 4.4_

- [ ] 10. Wire health scoring into the Analysis API
  - [ ] 10.1 Add `HEALTH_SCORING_ENABLED` flag and additive health block
    - In `app/main.py` read `HEALTH_SCORING_ENABLED` from env; when enabled, reuse the already-fetched `readme` and `analyze_repository` output, call `extract_signals` then `compute_health_score`, and attach a `health` block with `available: true`, `composite`, and five `{category, score, missing_data}` entries
    - Keep `repo`, `analysis`, `includes_code` present and unchanged; do not issue new external analysis calls for scoring
    - On any extraction/scoring exception (or when disabled) attach `health: {available: false, reason: ...}` (or omit when disabled) while preserving all existing fields
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 10.2 Write API integration tests (FastAPI TestClient, mocked fetchers)
    - Enabled: response has `health` with composite + five numeric in-range category scores (7.1, 7.2)
    - Additive: `repo`, `analysis`, `includes_code` unchanged alongside `health` (7.3)
    - Reuse: assert no fetches beyond the existing analysis flow via mock call counts (7.4)
    - Missing data: forced-unavailable signal surfaces `missing_data: true` (7.5)
    - Failure path: forced extraction/scoring exception yields existing fields plus `health.available == false` with a reason (7.6)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [ ] 11. Render the health score on the dashboard
  - [ ] 11.1 Implement dashboard render helpers and wiring
    - In `app/ui.py` render the composite score and per-category breakdown of all five categories in one result view when `health.available` is true
    - Show a "limited data" indicator next to any category with `missing_data: true`; show the unavailability reason and no scores when `health.available` is false; reuse the existing spinner as the in-progress indicator
    - Factor rendering into a testable helper (pure of Streamlit side effects where practical)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 11.2 Write example tests for the render helpers
    - Composite + full breakdown produced from an available payload; limited-data indicator on `missing_data` categories; unavailability reason with no scores from an unavailable payload
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 12. Add import-boundary and no-network guard tests
  - [ ]* 12.1 Write import-boundary and no-network tests for the pure core
    - Assert `app/health` pure modules import none of `requests`, `fastapi`, `streamlit`, `openai`, `app.main`
    - Assert `parse_pinning` and `extract_signals` (pinning derivation) issue no network request, verified by monkeypatching/mocking the network layer
    - _Requirements: 1.5, 4.5, 5.5_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; the model MUST NOT auto-implement them.
- The implementation language is Python; testing uses `pytest` + `hypothesis` (new dev dependencies, pinned to exact versions, excluded from the runtime backend).
- Property tests live in `tests/health/test_properties.py`, one test per property, minimum 100 iterations, tagged `# Feature: repo-health-score, Property {n}: {text}`.
- Each property sub-task is annotated with its property number and the requirement clause(s) it validates, and is placed close to the implementation it exercises to catch errors early.
- The pure core is built and tested in isolation (Tasks 1–8) before the analyzer refactor (Task 9), API wiring (Task 10), and dashboard (Task 11) depend on it, so there is no orphaned code.
- Since `pytest`/`hypothesis` are new dev dependencies, run the suite manually (e.g. `pytest tests/health`) rather than in a watcher.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "4.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5", "4.2", "4.3", "4.4", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "9.1"] },
    { "id": 5, "tasks": ["5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11", "5.12", "5.13", "7.1", "9.2"] },
    { "id": 6, "tasks": ["7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12", "7.13", "9.3", "12.1"] },
    { "id": 7, "tasks": ["9.4", "10.1"] },
    { "id": 8, "tasks": ["10.2", "11.1"] },
    { "id": 9, "tasks": ["11.2"] }
  ]
}
```
