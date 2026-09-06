# RepoPilot

**RepoPilot** is a lightweight AI-assisted repository documentation tool.

It analyzes a repository's README file and source code to generate a structured, easy-to-understand explanation of the project.

The tool supports both **README-only analysis** and **comprehensive code analysis**, providing flexible options for understanding repositories of different sizes and complexities.

---

## Overview

RepoPilot helps users quickly understand what a software project is about by using AI to interpret repository documentation and source code.

RepoPilot:

* reads the content of a repository `README.md`
* optionally analyzes source code structure and key files
* sends the content to an AI-powered backend
* generates a structured explanation of the project
* presents the result in a simple Streamlit-based user interface

This makes RepoPilot useful as an early-stage developer assistant for repository discovery, onboarding, and documentation review.

---

## Current Features

This version includes:

* **README Analysis** - Core feature that analyzes the README file
* **Code Analysis** - NEW: Analyzes repository structure, file organization, and code patterns
* **Source Code Inspection** - NEW: Fetches key files to understand implementation details
* **Language Detection** - NEW: Identifies programming languages used in the repository
* **Repo Health Score** - NEW: Computes a composite 0–100 health score with a five-category breakdown (README Quality, Documentation, Test Coverage, Dependency Freshness, README-vs-Code Consistency)
* **Structured Output** - AI-generated explanations covering architecture, tech stack, and key concepts

---

## Architecture

RepoPilot consists of two main components:

```text
RepoPilot
│
├── Backend
│   └── FastAPI service
│       ├── receives README and code analysis requests
│       ├── fetches code from GitHub API
│       ├── prepares the AI prompt
│       ├── calls the OpenAI API
│       └── returns the generated explanation
│
└── Frontend
    └── Streamlit app
        ├── provides the user interface
        ├── accepts repository input
        ├── offers code analysis toggle
        ├── sends requests to the backend
        └── displays the AI-generated result
```

---

## Technology Stack

RepoPilot uses a simple Python-based stack:

| Layer          | Technology    | Purpose                                |
| -------------- | ------------- | -------------------------------------- |
| Frontend       | Streamlit     | Provides the user interface            |
| Backend        | FastAPI       | Exposes the API endpoint for analysis  |
| API Server     | Uvicorn       | Runs the FastAPI backend locally       |
| AI Integration | OpenAI API    | Generates the repository explanation   |
| Configuration  | python-dotenv | Loads environment variables            |
| HTTP Client    | requests      | Enables frontend-backend communication |
| Testing        | pytest        | Runs the test suite                    |
| Property Tests | Hypothesis    | Property-based testing of scoring core |

---

## How It Works

The analysis flow works as follows:

1. The user starts the FastAPI backend.
2. The user starts the Streamlit frontend.
3. The user provides repository owner and name in the frontend.
4. The user optionally enables code analysis.
5. The frontend sends the request to the backend.
6. The backend fetches README and optionally analyzes code structure.
7. The AI generates a comprehensive explanation.
8. The frontend displays the result to the user.

```text
User
 │
 ▼
Streamlit Frontend
 │
 ├─→ (README Request) ─┐
 │                      │
 ├─→ (Code Analysis Request) ─→ GitHub API
 │                      │
 └─→ FastAPI Backend ───┘
                      │
                      ▼
                   OpenAI API
                      │
                      ▼
         Generated Repository Explanation
```

---

## Project Structure

A typical project structure looks like this:

```text
RepoPilot/
│
├── app/
│   ├── main.py              # FastAPI backend entry point
│   ├── ai.py                # OpenAI integration
│   ├── github_loader.py     # GitHub README fetching
│   ├── code_analyzer.py     # NEW: Code analysis and structure extraction
│   ├── ui.py                # Streamlit frontend
│   └── health/              # NEW: Repo health score (pure scoring core, signals, weights)
│
├── tests/
│   └── health/              # NEW: pytest + hypothesis property-based test suite
│
├── requirements.txt         # Python dependencies
├── requirements-dev.txt     # Dev/test dependencies (pytest, hypothesis)
├── pytest.ini              # pytest configuration
├── README.md               # This file
├── .env.example            # Environment variable template
└── .gitignore

```

---

## Prerequisites

Before running RepoPilot, make sure you have:

* Python 3.8+ installed
* Visual Studio Code or another code editor
* an OpenAI API key
* a local virtual environment
* the required Python packages installed

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/RepoPilot.git
cd RepoPilot
```

### 2. Create a virtual environment

On Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the root directory:

```env
OPENAI_API_KEY=your_openai_api_key_here
HEALTH_SCORING_ENABLED=true
```

`HEALTH_SCORING_ENABLED` toggles the Repo Health Score feature. It defaults to off in the backend when the variable is unset, so set it to `true` to include the score in the analysis.

Do not commit your `.env` file to GitHub.

---

## Running the Application

RepoPilot uses two separate terminals: one for the backend and one for the frontend.

### Terminal 1: Start the backend

```bash
uvicorn app.main:app --reload
```

The backend usually runs on:

```text
http://127.0.0.1:8000
```

### Terminal 2: Start the frontend

```bash
streamlit run app/ui.py
```

The frontend usually opens in your browser at:

```text
http://localhost:8501
```

### Running the tests

Install the dev/test dependencies, then run the health-score test suite:

```bash
pip install -r requirements-dev.txt
pytest tests/health -q
```

---

## Usage

1. Enter the GitHub repository owner/organization and repository name
2. Optionally enable "Include code analysis" for deeper insights
3. Click "Analyze repository"
4. View the generated analysis

### Example

- Owner: `psf`
- Repository: `requests`
- With code analysis: Provides insights into HTTP handling patterns, request architecture, etc.

---

## Features Explained

### README Analysis
- Extracts and interprets the README content
- Suitable for quick overviews of any public repository

### Code Analysis (Optional)
- Scans repository structure and file organization
- Identifies programming languages and key files
- Fetches and previews main implementation files
- Provides insights into architecture and design patterns
- Useful for understanding project organization and code structure

### Repo Health Score
Computes a single composite score from 0 to 100 with a per-category breakdown, shown on the Streamlit dashboard alongside the existing analysis. The score covers five categories:

- **README Quality** - completeness of the README (length, headings, code blocks, install and usage sections)
- **Documentation** - presence of documentation files (Markdown/reStructuredText and `docs/` entries)
- **Test Coverage** - presence of test files and CI configuration
- **Dependency Freshness** - presence of a dependency lockfile/manifest and how many declared dependencies are pinned vs unpinned
- **README-vs-Code Consistency** - how well technologies mentioned in the README match what is actually detected in the code (languages, key files, and file-presence indicators like Docker/Terraform/CI workflows)

Category weights are configuration-driven, so they can be tuned without changing the scoring logic. The scoring computation is a pure function that operates on already-collected signals (README text + code structure) and introduces no new external GitHub API calls. Categories scored from unavailable signals are flagged as limited data rather than shown as a confident zero.

The feature is opt-in: set `HEALTH_SCORING_ENABLED=true` to enable it.
