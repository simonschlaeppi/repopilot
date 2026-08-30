from fastapi import FastAPI, HTTPException
from app.github_loader import get_readme
from app.code_analyzer import get_code_summary
from app.ai import explain_repo, explain_repo_with_code

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def home():
    return {"message": "RepoPilot is running"}

@app.get("/analyze")
def analyze(owner: str, repo: str, include_code: bool = False):
    readme = get_readme(owner, repo)
    
    if include_code:
        code_summary = get_code_summary(owner, repo)
        explanation = explain_repo_with_code(readme, code_summary)
    else:
        explanation = explain_repo(readme)
    
    return {"repo": f"{owner}/{repo}", "analysis": explanation, "includes_code": include_code}