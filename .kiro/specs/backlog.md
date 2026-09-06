# RepoPilot Backlog

This file is a lightweight, cross-cutting home for deferred ("someday") improvement ideas that are **not yet scoped into a spec**. Items here have been consciously set aside — often called out as out-of-scope in an existing spec — and are parked so they are not lost. When an item is picked up, it graduates into its own spec under `.kiro/specs/` and should be removed from (or linked out of) this list.

Each entry is a one-line title plus a short description and, where relevant, a pointer to the spec it originated from.

## Deferred Ideas

- **Group B consistency detection (framework/ecosystem terms)** — Extend README-vs-code consistency to recognize framework/ecosystem terms parsed from manifest dependency *contents* (e.g. React, Django, FastAPI named as dependencies), rather than only file-presence indicators. Group A (file-presence indicators such as Docker, Terraform, CI workflows) is implemented; Group B requires parsing manifest dependency lists and deciding what counts as "detected." _Originating spec: `repo-health-score`._

- **Historical score tracking** — Track a repository's health score over time so trends and regressions are visible, instead of only a point-in-time score. _Originating spec: `repo-health-score` (out of scope)._

- **Multi-repository comparison / ranking** — Compare or rank multiple repositories against each other on their health scores, rather than scoring a single repository in isolation. _Originating spec: `repo-health-score` (out of scope)._

- **New external data sources** — Introduce additional external data sources or scraping beyond what RepoPilot already collects (currently README text and code-structure analysis from the existing GitHub fetch flow). _Originating spec: `repo-health-score` (out of scope)._
