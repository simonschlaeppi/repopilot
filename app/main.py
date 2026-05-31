from fastapi import FastAPI
from app.github_loader import get_readme
from app.ai import explain_repo

app = FastAPI()

@app.get("/")
def home():
    return {"message": "RepoPilot is running"}

@app.get("/analyze")
def analyze(owner: str, repo: str):
    readme = get_readme(owner, repo)
    explanation = explain_repo(readme)
    return {"repo": f"{owner}/{repo}", "analysis": explanation}