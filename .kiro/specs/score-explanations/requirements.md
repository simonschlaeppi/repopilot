# Requirements Document

## Introduction

The Score Explanations feature makes RepoPilot's repository health score interpretable. Today the score surfaces a composite value (0–100) and five per-category scores, but a user is left to guess what each category measures and why a particular repository earned the score it did. This feature adds two related capabilities:

1. **Category descriptions (static):** Human-readable text describing what each of the five score categories measures, independent of any specific repository. This lets a user understand the meaning of README_Quality, Documentation, Test_Coverage, Dependency_Freshness, and Readme_Code_Consistency without inspecting a scored repo.

2. **Runtime score explanations (dynamic, per-repo):** For a scored repository, a short human-readable rationale for why each category received its score — for example "Test Coverage 35/100: no test files detected, but a CI workflow is present." For categories derived from unavailable signals (`missing_data=True`), the explanation states that the signal was unavailable (limited data) rather than presenting a confident zero.

Both capabilities are surfaced through the Analysis API response and rendered on the Streamlit dashboard alongside the existing health-score breakdown.

A central design constraint drives these requirements: explanations must stay consistent with the actual scoring logic and thresholds already implemented in the scoring core (`app/health/scoring.py`). Each per-category scoring helper (`_score_readme_quality`, `_score_documentation`, `_score_test_coverage`, `_score_dependency_freshness`, `_score_consistency`) already encodes the exact rationale — for example Test_Coverage combines test-file credit and CI credit, Dependency_Freshness scales with the pinned fraction, and Readme_Code_Consistency scales with the match ratio. The runtime explanations must derive from these same signals and thresholds so the explanatory text can never contradict the math that produced the score. Explanation generation, like scoring, must remain pure and deterministic (standard library plus `app.health` only) and must introduce no new external GitHub API calls, consuming only the already-computed signals and results.

### Scope Notes

- **In scope:** A static description for each of the five categories; a pure, deterministic per-category runtime explanation derived from the already-computed Repository_Signals and category score; an explicit limited-data explanation for categories with `missing_data=True`; exposing both descriptions and explanations through the Analysis API; and rendering both on the dashboard.
- **Out of scope:** Changing how any score is computed or weighted; adding new signals, external data sources, or network calls; historical tracking of scores; comparing or ranking multiple repositories. Additional deferred ideas are tracked in `.kiro/specs/backlog.md`.

## Glossary

- **Category**: One of the five defined scoring dimensions: README_Quality, Documentation, Test_Coverage, Dependency_Freshness, Readme_Code_Consistency.
- **Category_Description**: Static, repository-independent human-readable text describing what a single Category measures and which signals contribute to it.
- **Score_Explanation**: A short, repository-specific human-readable rationale for the Category_Score a single Category received for a scored repository.
- **Explanation_Generator**: The pure, deterministic component that produces a Score_Explanation for each Category from the already-computed Repository_Signals and Category_Score. It performs no network request and imports only the standard library and `app.health` modules.
- **Repository_Signals**: The structured raw inputs to scoring already produced by the Signal_Extractor (README metrics, detected documentation/test/CI/lockfiles, language counts, key files, dependency pinning counts, consistency ratio, and per-category availability flags).
- **Category_Score**: A single category's computed result, comprising an integer score in the range 0 to 100 inclusive and a missing-data indicator.
- **Missing_Data_Indicator**: The per-category boolean (`missing_data`) that is true when a Category_Score was derived from an unavailable signal rather than from confident data.
- **Scoring_Core**: The existing pure scoring implementation in `app.health.scoring`, whose per-category helpers encode the thresholds and rationale that a Score_Explanation must remain consistent with.
- **Analysis_API**: The existing FastAPI backend that exposes repository analysis endpoints and the health-score block.
- **Dashboard**: The existing Streamlit frontend that displays analysis and health-score results.
- **Limited_Data**: The condition in which a Category_Score has its Missing_Data_Indicator set to true, meaning the underlying signal was unavailable and the score is not a confident zero.

## Requirements

### Requirement 1: Provide Static Category Descriptions

**User Story:** As a RepoPilot user, I want a plain-language description of what each score category measures, so that I understand the meaning of each category without inspecting a specific repository.

#### Acceptance Criteria

1. THE Explanation_Generator SHALL provide exactly one Category_Description for each of the five categories README_Quality, Documentation, Test_Coverage, Dependency_Freshness, and Readme_Code_Consistency.
2. THE Explanation_Generator SHALL express each Category_Description as non-empty human-readable text that states what the associated Category measures.
3. THE Explanation_Generator SHALL produce each Category_Description independent of any specific repository's Repository_Signals or Category_Score.
4. THE Explanation_Generator SHALL describe in each Category_Description the signals that contribute to the associated Category consistent with the signals used by the corresponding Scoring_Core helper for that Category.
5. WHEN requested for the same Category, THE Explanation_Generator SHALL return an identical Category_Description on every invocation.

### Requirement 2: Generate Per-Category Runtime Explanations

**User Story:** As a RepoPilot user, I want a short rationale for why each category received its score on a specific repository, so that I can act on the result.

#### Acceptance Criteria

1. THE Explanation_Generator SHALL produce one Score_Explanation for each of the five categories from the already-computed Repository_Signals and that category's Category_Score.
2. THE Explanation_Generator SHALL express each Score_Explanation as non-empty human-readable text that references the Category_Score value for the associated Category.
3. THE Explanation_Generator SHALL derive each Score_Explanation from the same Repository_Signals fields and thresholds that the corresponding Scoring_Core helper uses to compute that Category_Score.
4. WHEN the Test_Coverage Category_Score is generated, THE Explanation_Generator SHALL state in the Score_Explanation whether test files were detected and whether a CI configuration was detected, consistent with the test-file and CI signals in Repository_Signals.
5. WHEN the Dependency_Freshness Category_Score is generated, THE Explanation_Generator SHALL state in the Score_Explanation the detected total dependency count and unpinned dependency count from Repository_Signals.
6. WHEN the Readme_Code_Consistency Category_Score is generated, THE Explanation_Generator SHALL state in the Score_Explanation the README-vs-code match outcome consistent with the consistency ratio in Repository_Signals.
7. WHEN the README_Quality Category_Score is generated, THE Explanation_Generator SHALL state in the Score_Explanation which README completeness signals from Repository_Signals were present.
8. WHEN the Documentation Category_Score is generated, THE Explanation_Generator SHALL state in the Score_Explanation whether documentation files were detected from Repository_Signals.

### Requirement 3: Explain Limited-Data Categories Distinctly

**User Story:** As a RepoPilot user, I want categories scored with limited data to be explained as unavailable rather than as a confident zero, so that I do not misread a missing signal as a failing result.

#### Acceptance Criteria

1. WHERE a Category_Score has its Missing_Data_Indicator set to true, THE Explanation_Generator SHALL produce a Score_Explanation stating that the underlying signal was unavailable or limited for that Category.
2. IF a Category_Score has its Missing_Data_Indicator set to true, THEN THE Explanation_Generator SHALL exclude any statement in the Score_Explanation that attributes the score to a confident or measured zero result.
3. WHERE a Category_Score has its Missing_Data_Indicator set to false, THE Explanation_Generator SHALL produce a Score_Explanation attributing the score to the measured signals for that Category.

### Requirement 4: Keep Explanation Generation Pure and Consistent with Scoring

**User Story:** As a RepoPilot developer, I want explanations to be pure, deterministic, and sourced from the scoring signals, so that they never drift from the actual scoring math or introduce new external calls.

#### Acceptance Criteria

1. THE Explanation_Generator SHALL produce every Category_Description and Score_Explanation without issuing any external network request.
2. THE Explanation_Generator SHALL depend only on the standard library and the `app.health` modules.
3. WHEN provided identical Repository_Signals and Category_Score inputs, THE Explanation_Generator SHALL produce identical Score_Explanation text on every computation.
4. THE Explanation_Generator SHALL derive each Score_Explanation from the Repository_Signals and Category_Score values rather than from thresholds duplicated independently of the Scoring_Core.
5. THE Explanation_Generator SHALL produce a Score_Explanation whose stated rationale is consistent with the direction of the associated Category_Score, so that a higher-scoring signal is not described as a weakness and a lower-scoring signal is not described as a strength.

### Requirement 5: Expose Descriptions and Explanations via the Analysis API

**User Story:** As a Dashboard developer, I want descriptions and explanations available from the backend, so that the frontend can display them alongside the existing scores.

#### Acceptance Criteria

1. WHERE health scoring is enabled for a repository analysis request, THE Analysis_API SHALL return a Category_Description and a Score_Explanation for each of the five categories in the response.
2. THE Analysis_API SHALL return each Category_Description and each Score_Explanation as a text field associated with its category, in addition to the existing per-category score and missing-data fields.
3. WHERE health scoring is enabled, THE Analysis_API SHALL return the description and explanation fields in addition to the existing analysis and health-score response fields without removing or altering those existing fields.
4. WHEN health scoring produces descriptions and explanations, THE Analysis_API SHALL reuse the already-collected Repository_Signals and computed Category_Score values for that request rather than issuing new external analysis calls.
5. IF signal extraction or score computation fails for a repository, THEN THE Analysis_API SHALL return the existing analysis response fields with the health score marked unavailable and SHALL omit per-category Score_Explanation fields for that request.

### Requirement 6: Display Descriptions and Explanations on the Dashboard

**User Story:** As a RepoPilot user, I want to see each category's description and its per-repository explanation on the dashboard, so that I understand both what each category means and why the repository scored as it did.

#### Acceptance Criteria

1. WHEN the Analysis_API returns Category_Description values, THE Dashboard SHALL display the Category_Description for each of the five categories alongside that category's name and score.
2. WHEN the Analysis_API returns Score_Explanation values, THE Dashboard SHALL display the Score_Explanation for each of the five categories alongside that category's score.
3. WHERE a Category_Score is marked with its Missing_Data_Indicator set to true, THE Dashboard SHALL display the limited-data Score_Explanation for that Category alongside the existing limited-data indicator.
4. IF the Analysis_API reports that the health score is unavailable, THEN THE Dashboard SHALL display the descriptive unavailability reason and SHALL NOT display per-category Score_Explanation values.
