# Requirements Document

## Introduction

The Repo Health Score feature adds a single composite health score (0–100) with a per-category breakdown to RepoPilot. The score aggregates signals that RepoPilot already derives from a GitHub repository (README content and source file structure) into one interpretable number plus category-level detail, displayed on the Streamlit dashboard.

The score is computed from five categories: README quality/completeness, documentation presence, test coverage indicators, dependency freshness (based on how dependencies are pinned), and README-vs-code consistency. Category weights are configuration-driven so they can be tuned later without code changes. The scoring computation is a pure function that operates on already-collected raw signals, so no new external GitHub API calls or scraping are introduced for scoring itself.

A key precondition surfaced during codebase review: today RepoPilot's code analyzer produces only a free-text summary and discards its internal structured data (file list, detected languages, key files). File-presence signals (test files, CI configuration, lockfiles) and a README-vs-code consistency signal are not currently surfaced in any structured form. This feature therefore requires exposing structured signal data from the existing analysis so the pure scoring function can consume it.

### Scope Notes

- **In scope:** Extracting structured signals from existing README and code-structure analysis, a pure scoring function, config-driven category weights, an API surface for the score, and a dashboard breakdown view.
- **Out of scope:** Historical tracking of a repository's score over time; comparing or ranking multiple repositories against each other; introducing new external data sources or scraping beyond what RepoPilot already collects; framework/ecosystem consistency detection that parses manifest dependency contents ("Group B" technology references such as React/Django/FastAPI named as dependencies), which is deferred to a future requirement.

## Glossary

- **Health_Score_Service**: The pure computation component that accepts structured repository signals and configured weights and produces a composite score and category breakdown. Has no dependency on the API layer, UI, or network.
- **Signal_Extractor**: The component that transforms already-collected README text and code-structure data into a structured Repository_Signals object. Reuses existing analysis output and performs no new external calls.
- **Repository_Signals**: A structured data object holding the raw inputs to scoring: README text/metrics, detected documentation files, detected test files, detected CI configuration files, detected dependency/lockfiles with pinned-version data, detected languages, and detected key files.
- **Category_Score**: A normalized score in the range 0–100 for a single scoring category.
- **Composite_Health_Score**: An integer in the range 0–100 formed by combining all Category_Score values according to configured weights.
- **Weight_Config**: A configuration structure (dictionary/constants) mapping each scoring category to a numeric weight, defined in a dedicated configuration module rather than inline in scoring logic.
- **Category**: One of the five defined scoring dimensions: README_Quality, Documentation, Test_Coverage, Dependency_Freshness, Readme_Code_Consistency.
- **Analysis_API**: The existing FastAPI backend that exposes repository analysis endpoints.
- **Dashboard**: The existing Streamlit frontend that displays analysis results.
- **Lockfile**: A dependency lock or manifest file used to detect dependency pinning (for example requirements.txt, package.json, pyproject.toml, go.mod, Cargo.toml).
- **CI_Config**: A continuous-integration configuration artifact detectable from the file tree (for example a .github workflows path).
- **Unpinned_Dependency**: A dependency declaration that uses a wildcard, an open or unbounded version range, or no version specifier at all, detected directly from Lockfile contents with no external registry lookup. The presence of Unpinned_Dependency instances is a negative signal for Dependency_Freshness.
- **Signal_Classification_Set**: The set of repository file paths made available to the Signal_Extractor for classification into documentation, test, CI_Config, and Lockfile groups. This is distinct from the smaller set of files whose contents are fetched for the AI code summary; membership is governed by the classification predicates, not by the content-fetch budget.
- **Content_Fetch_Budget**: The maximum number of non-lockfile file contents fetched for the AI code summary (historically the `max_files` cap). It bounds content fetching only; it does not bound which file paths are available for signal classification.
- **Technology_Reference_Vocabulary**: The deterministic, offline set of terms the Signal_Extractor recognizes as detectable technology references in README text. It includes known language names, known key-file/manifest names, and a curated set of file-type/config indicators ("Group A": for example Docker, Makefile, Terraform, GraphQL, Kubernetes/Helm, CI workflows), where each Group A term is paired with a concrete file-presence detector so a mention only matches when the corresponding artifact exists in the file tree. It requires no network access. Framework/ecosystem terms detected by parsing manifest dependency *contents* ("Group B": for example React, Django, FastAPI named as dependencies) are out of scope for this vocabulary and deferred to a future requirement.

## Requirements

### Requirement 1: Extract Structured Repository Signals

**User Story:** As a RepoPilot developer, I want the existing README and code-structure analysis to be exposed as a structured signals object, so that the health score can be computed from reusable data without re-fetching or scraping.

#### Acceptance Criteria

1. THE Signal_Extractor SHALL produce a Repository_Signals object from README text and code-structure data already collected by RepoPilot.
2. THE Signal_Extractor SHALL populate Repository_Signals with the README text, a list of detected documentation files, a list of detected test files, a list of detected CI_Config files, a list of detected Lockfiles, a mapping of detected languages to their file counts, and a list of detected key files.
3. THE Signal_Extractor SHALL classify a detected file as a documentation file when its path matches a documentation file pattern (for example a Markdown file or a docs directory entry), as a test file when its path matches a test file or test directory pattern, as a CI_Config file when its path matches a recognized CI configuration location, and as a Lockfile when its name matches a recognized dependency manifest or lock file.
4. WHERE the code-structure analysis currently returns only formatted text, THE Signal_Extractor SHALL obtain the structured file list, language counts, and key-file list from structured data rather than by parsing the formatted text summary.
5. THE Signal_Extractor SHALL derive all Repository_Signals fields without issuing external network requests to services other than the existing GitHub repository fetch flow.
6. IF a required source input (README text or code-structure data) is absent, THEN THE Signal_Extractor SHALL populate the corresponding Repository_Signals fields with empty collections or empty strings and SHALL set the per-field availability indicator for each affected field to false.
7. WHEN a Repository_Signals field is successfully populated from available source data, THE Signal_Extractor SHALL set that field's availability indicator to true.
8. THE Signal_Classification_Set SHALL include every repository file path that matches a documentation, test, CI_Config, or Lockfile classification pattern, independent of whether that path is selected for AI code-summary content fetching, so that classification under Requirement 1.3 is not suppressed by content-selection heuristics (for example a heuristic that excludes Markdown files).
9. THE size of the Signal_Classification_Set SHALL NOT be limited by the Content_Fetch_Budget; the Content_Fetch_Budget SHALL bound only the number of file contents fetched for the AI code summary and SHALL NOT reduce which file paths are available for signal classification.

### Requirement 2: Compute Category Scores from Signals

**User Story:** As a RepoPilot user, I want each health category scored individually, so that I can see which aspects of a repository are strong or weak.

#### Acceptance Criteria

1. THE Health_Score_Service SHALL compute a README_Quality Category_Score as an integer in the range 0 to 100 inclusive from the README completeness metrics in Repository_Signals, where more complete README content yields a higher score.
2. THE Health_Score_Service SHALL compute a Documentation Category_Score as an integer in the range 0 to 100 inclusive from the presence of documentation files in Repository_Signals, where the presence of documentation files yields a higher score than their absence.
3. THE Health_Score_Service SHALL compute a Test_Coverage Category_Score as an integer in the range 0 to 100 inclusive from the presence of test files and CI_Config in Repository_Signals, where the presence of test files and CI_Config each increase the score.
4. THE Health_Score_Service SHALL compute a Dependency_Freshness Category_Score as an integer in the range 0 to 100 inclusive from Lockfile presence and Unpinned_Dependency counts in Repository_Signals, where Lockfile presence increases the score and each detected Unpinned_Dependency reduces the score.
5. THE Health_Score_Service SHALL compute a Readme_Code_Consistency Category_Score as an integer in the range 0 to 100 inclusive from the Readme_Code_Consistency match ratio in Repository_Signals, where a higher match ratio yields a higher score.
6. IF a category's underlying signal is marked unavailable for a repository, THEN THE Health_Score_Service SHALL assign that Category_Score the default value 0 and SHALL set that Category_Score's missing-data indicator to true.
7. WHEN provided identical Repository_Signals and Weight_Config inputs, THE Health_Score_Service SHALL produce identical Category_Score values on every computation.

### Requirement 3: Derive README-vs-Code Consistency Signal

**User Story:** As a RepoPilot user, I want a consistency signal derived from available data, so that the health score reflects README-vs-code alignment even though no ready-made consistency signal exists today.

#### Acceptance Criteria

1. THE Signal_Extractor SHALL treat a README token as a detectable technology reference when it matches, case-insensitively, an entry in the Technology_Reference_Vocabulary (which includes known language names, known key-file/manifest names, and a curated set of file-type/config indicators, each paired with a file-presence detector).
2. THE Signal_Extractor SHALL derive the set of unique detectable technology references from the README text by deduplicating matched references case-insensitively.
3. WHEN a unique README technology reference matches a detected language, a detected key file, or a detected framework/tooling/file-type indicator in the code structure, THE Signal_Extractor SHALL count that reference as a consistency match.
4. WHEN a unique README technology reference does not match any detected language, detected key file, or detected framework/tooling/file-type indicator in the code structure, THE Signal_Extractor SHALL count that reference as a consistency mismatch.
5. THE Signal_Extractor SHALL express the Readme_Code_Consistency signal as the ratio of consistency matches to the total count of unique detectable technology references, as a value in the range 0.0 to 1.0 inclusive.
6. IF the README contains no detectable technology references, THEN THE Signal_Extractor SHALL set the Readme_Code_Consistency signal availability indicator to false, so that the category is reported as derived from missing data (limited-data) per Requirements 2.6 and 7.5 rather than as a confident zero score.
7. IF the detected code structure contains no languages and no key files, THEN THE Signal_Extractor SHALL set the Readme_Code_Consistency signal availability indicator to false.

### Requirement 4: Derive Unpinned Dependency Signal from Lockfiles

**User Story:** As a RepoPilot user, I want dependency freshness derived from how dependencies are pinned in local lockfiles, so that the health score reflects dependency hygiene without any external registry lookup.

#### Acceptance Criteria

1. THE Signal_Extractor SHALL parse the contents of each detected Lockfile to identify individual dependency declarations.
2. WHEN a dependency declaration specifies an exact version, THE Signal_Extractor SHALL classify that dependency as pinned.
3. WHEN a dependency declaration uses a wildcard, an open or unbounded version range, or no version specifier at all, THE Signal_Extractor SHALL classify that dependency as an Unpinned_Dependency.
4. THE Signal_Extractor SHALL record the count of Unpinned_Dependency instances and the total count of dependency declarations across all detected Lockfiles in Repository_Signals.
5. THE Signal_Extractor SHALL derive the Unpinned_Dependency signal without issuing any external network request.
6. IF no Lockfile is detected in the code structure, THEN THE Signal_Extractor SHALL set the Dependency_Freshness signal availability indicator to false.

### Requirement 5: Compute Composite Health Score

**User Story:** As a RepoPilot user, I want a single 0–100 health score, so that I can quickly gauge overall repository health.

#### Acceptance Criteria

1. THE Health_Score_Service SHALL compute the Composite_Health_Score as the weighted sum of all five Category_Score values using the normalized weights from Weight_Config.
2. THE Health_Score_Service SHALL produce a Composite_Health_Score that is an integer in the range 0 to 100 inclusive, rounding the weighted sum to the nearest integer with halves rounded up.
3. THE Health_Score_Service SHALL return the Composite_Health_Score together with every individual Category_Score in a single result object.
4. THE Health_Score_Service SHALL compute the Composite_Health_Score as a pure function whose output depends only on the provided Repository_Signals and Weight_Config inputs.
5. THE Health_Score_Service SHALL operate without importing or invoking the Analysis_API layer or the Dashboard.
6. IF any provided Category_Score is outside the range 0 to 100 inclusive, THEN THE Health_Score_Service SHALL raise a defined error identifying the offending category and SHALL NOT return a Composite_Health_Score.

### Requirement 6: Configuration-Driven Category Weights

**User Story:** As a RepoPilot maintainer, I want category weights defined in configuration, so that I can tune scoring later without editing the scoring logic.

#### Acceptance Criteria

1. THE Weight_Config SHALL define, in a configuration module separate from the scoring computation code, exactly one non-negative numeric weight for each of the five categories README_Quality, Documentation, Test_Coverage, Dependency_Freshness, and Readme_Code_Consistency.
2. THE Health_Score_Service SHALL read all category weights from Weight_Config at computation time rather than from values embedded in the scoring computation.
3. WHEN Weight_Config values are changed and a new computation is requested, THE Health_Score_Service SHALL use the current Weight_Config values for that computation without requiring a change to, or recompilation of, the scoring computation code.
4. IF the five Weight_Config category weights sum to a total other than 1.0, THEN THE Health_Score_Service SHALL normalize each weight by dividing it by the sum of all five weights so the normalized weights sum to 1.0, before computing the Composite_Health_Score.
5. IF Weight_Config omits a weight for a required category, THEN THE Health_Score_Service SHALL raise a defined configuration error identifying the missing category by name and SHALL NOT compute the Composite_Health_Score.
6. IF any Weight_Config category weight is negative, non-numeric, or the five weights sum to zero, THEN THE Health_Score_Service SHALL raise a defined configuration error indicating the invalid weight condition and SHALL NOT compute the Composite_Health_Score.

### Requirement 7: Expose Health Score via the Analysis API

**User Story:** As a Dashboard developer, I want the health score available from the backend, so that the frontend can display it alongside existing analysis.

#### Acceptance Criteria

1. WHERE health scoring is enabled for a repository analysis request, THE Analysis_API SHALL return the Composite_Health_Score and a Category_Score value for each of the five defined categories (README_Quality, Documentation, Test_Coverage, Dependency_Freshness, Readme_Code_Consistency) in the response.
2. THE Analysis_API SHALL return the Composite_Health_Score as an integer numeric field in the range 0 to 100 inclusive and each Category_Score as a numeric field in the range 0 to 100 inclusive, rather than as free text.
3. WHERE health scoring is enabled, THE Analysis_API SHALL return the health score fields in addition to the existing analysis response fields without removing or altering those existing fields.
4. WHEN health scoring uses signals from a repository, THE Analysis_API SHALL reuse the already-collected README and code-structure data for that request rather than issuing new external analysis calls for scoring.
5. WHERE a Category_Score was derived from missing data, THE Analysis_API SHALL include a per-category indicator marking that Category_Score as derived from missing data.
6. IF signal extraction or score computation fails for a repository, THEN THE Analysis_API SHALL return the existing analysis response fields together with a health score indicator set to unavailable and a reason describing the failure cause, without omitting the existing analysis fields.

### Requirement 8: Display Health Score and Category Breakdown on the Dashboard

**User Story:** As a RepoPilot user, I want to see the health score and its category breakdown on the dashboard, so that I understand both the overall score and its contributing factors.

#### Acceptance Criteria

1. WHEN the Analysis_API returns a Composite_Health_Score, THE Dashboard SHALL display the Composite_Health_Score as an integer value in the range 0 to 100 inclusive.
2. WHEN the Analysis_API returns Category_Score values, THE Dashboard SHALL display each of the five Category_Scores (README_Quality, Documentation, Test_Coverage, Dependency_Freshness, Readme_Code_Consistency) with its category name and its value in the range 0 to 100 inclusive.
3. WHEN the Analysis_API returns a scoring result, THE Dashboard SHALL display the per-category breakdown of all five Category_Scores together with the single Composite_Health_Score value in the same result view.
4. WHERE a Category_Score is marked as derived from missing data, THE Dashboard SHALL display a visible indicator alongside that Category_Score identifying it as scored with limited data.
5. IF the Analysis_API reports that the health score is unavailable, THEN THE Dashboard SHALL display the descriptive unavailability reason provided by the Analysis_API and SHALL NOT display a Composite_Health_Score or Category_Score values.
6. WHILE the Dashboard is awaiting the Analysis_API scoring response, THE Dashboard SHALL display an in-progress indicator and SHALL NOT display a Composite_Health_Score or Category_Score values.
