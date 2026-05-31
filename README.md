# RepoPilot

**RepoPilot** is a lightweight AI-assisted repository documentation tool.

It analyzes a repository README file and generates a structured, easy-to-understand explanation of the project.

The current version focuses on the **README-only analysis flow**. The backend and frontend are already separated, providing a clean foundation for extending the tool later with full repository and source-code analysis.

---

## Overview

RepoPilot helps users quickly understand what a software project is about by using AI to interpret the repository documentation.

In its current version, RepoPilot:

* reads the content of a repository `README.md`
* sends the README content to an AI-powered backend
* generates a structured explanation of the project
* presents the result in a simple Streamlit-based user interface

This makes RepoPilot useful as an early-stage developer assistant for repository discovery, onboarding, and documentation review.

---

## Current Scope

This version is the **README-Version** of RepoPilot.

That means:

* only the repository README is analyzed
* source code files are not analyzed yet
* no repository-wide dependency or architecture scanning is included yet
* the backend and frontend already run as separate components

Planned extensions may include source-code analysis, README-to-code comparison, architecture insights, and improvement suggestions.

---

## Architecture

RepoPilot consists of two main components:

```text
RepoPilot
│
├── Backend
│   └── FastAPI service
│       ├── receives README content
│       ├── prepares the AI prompt
│       ├── calls the OpenAI API
│       └── returns the generated explanation
│
└── Frontend
    └── Streamlit app
        ├── provides the user interface
        ├── accepts README input
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

---

## How It Works

The current README analysis flow works as follows:

1. The user starts the FastAPI backend.
2. The user starts the Streamlit frontend.
3. The user provides README content in the frontend.
4. The frontend sends the README content to the backend.
5. The backend creates a prompt for the OpenAI API.
6. The OpenAI API returns a structured explanation.
7. The frontend displays the result to the user.

```text
User
 │
 ▼
Streamlit Frontend
 │
 ▼
FastAPI Backend
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
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── app.py
│   └── requirements.txt
│
├── README.md
└── .gitignore
```

Depending on your local setup, filenames may differ slightly.

---

## Prerequisites

Before running RepoPilot, make sure you have:

* Python installed
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

Install the required dependencies for the backend and frontend.

Example:

```bash
pip install -r requirements.txt
```

If backend and frontend have separate requirement files:

```bash
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the backend folder:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

Do not commit your `.env` file to GitHub.

---

## Running the Application

RepoPilot uses two separate terminals: one for the backend and one for the frontend.

### Terminal 1: Start the backend

```bash
cd backend
uvicorn main:app --reload
```

The backend usually runs on:

```text
http://127.0.0.1:8000
```

### Terminal 2: Start the frontend

```bash
cd frontend
streamlit run app.py
```

The frontend usually opens in your browser at:

```text
http://localhost:8501
```
