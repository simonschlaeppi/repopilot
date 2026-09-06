import os

from fastapi import FastAPI, HTTPException
from app.github_loader import get_readme
from app.code_analyzer import get_code_summary, analyze_repository, _format_code_summary
from app.ai import explain_repo, explain_repo_with_code
from app.health.signals import extract_signals
from app.health.scoring import compute_health_score

app = FastAPI()

# Opt-in enablement flag (Req 7.1). Disabled by default so the /analyze
# response shape stays backward compatible unless explicitly turned on.
HEALTH_SCORING_ENABLED = os.getenv("HEALTH_SCORING_ENABLED", "false").lower() == "true"


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def home():
    return {"message": "RepoPilot is running"}

@app.get("/analyze")
def analyze(owner: str, repo: str, include_code: bool = False):
    readme = get_readme(owner, repo)

    # Reuse a single analyze_repository() call for BOTH the code-summary path
    # and health scoring so scoring introduces no new external analysis calls
    # beyond the existing flow (Req 7.4). structured_data stays None unless we
    # actually need it (include_code and/or scoring enabled).
    structured_data = None
    if HEALTH_SCORING_ENABLED or include_code:
        structured_data = analyze_repository(owner, repo)

    if include_code:
        # Derive the free-text summary from the already-fetched structured data
        # so get_code_summary's fetch flow is not duplicated.
        code_summary = _format_code_summary(structured_data)
        explanation = explain_repo_with_code(readme, code_summary)
    else:
        explanation = explain_repo(readme)

    # Existing response fields — present and unchanged (Req 7.3).
    response = {"repo": f"{owner}/{repo}", "analysis": explanation, "includes_code": include_code}

    if HEALTH_SCORING_ENABLED:
        try:
            signals = extract_signals(readme, structured_data)
            result = compute_health_score(signals)
            response["health"] = {
                "available": True,
                "composite": result.composite,
                "categories": [
                    {
                        "category": cs.category.value,
                        "score": cs.score,
                        "missing_data": cs.missing_data,
                    }
                    for cs in result.categories
                ],
            }
        except Exception as exc:  # noqa: BLE001 - any extraction/scoring failure degrades gracefully
            # Preserve all existing fields; surface unavailability + reason (Req 7.6).
            response["health"] = {"available": False, "reason": str(exc)}

    return response
