import streamlit as st
import requests

API_BASE_URL = "http://127.0.0.1:8000"

# Human-friendly labels for the five scoring categories. Keys are the
# canonical category values returned by the API's health block.
_CATEGORY_LABELS = {
    "README_Quality": "README Quality",
    "Documentation": "Documentation",
    "Test_Coverage": "Test Coverage",
    "Dependency_Freshness": "Dependency Freshness",
    "Readme_Code_Consistency": "README-vs-Code Consistency",
}


def build_health_view(health: dict | None) -> dict:
    """Pure helper that maps the API's ``health`` block to a render-ready view.

    No Streamlit (``st.*``) calls — depends only on the standard library so it
    can be imported and unit-tested independently of the dashboard.

    Returns a dict with a stable ``state`` discriminator:

    - ``{"state": "none"}`` when ``health`` is absent/None (scoring disabled):
      nothing health-related should be rendered.
    - ``{"state": "available", "composite": int, "rows": [
          {"category": str, "label": str, "score": int, "limited_data": bool}, ...
      ]}`` when ``health.available`` is true — the composite (Req 8.1) and all
      returned categories with name + score + a limited-data flag (Req 8.2-8.4).
    - ``{"state": "unavailable", "reason": str}`` when ``health.available`` is
      false — the unavailability reason and NO scores (Req 8.5).
    """
    if not health:
        return {"state": "none"}

    if not health.get("available"):
        reason = health.get("reason") or "Health score is unavailable."
        return {"state": "unavailable", "reason": reason}

    rows = []
    for cat in health.get("categories", []):
        category = cat.get("category", "")
        rows.append(
            {
                "category": category,
                "label": _CATEGORY_LABELS.get(category, category),
                "score": cat.get("score"),
                "limited_data": bool(cat.get("missing_data")),
            }
        )

    return {
        "state": "available",
        "composite": health.get("composite"),
        "rows": rows,
    }


def render_health(health: dict | None) -> None:
    """Render the health view using Streamlit, delegating all decisions to the
    pure ``build_health_view`` helper. Reuses the existing spinner as the
    in-progress indicator (Req 8.6) — no new spinner is introduced here."""
    view = build_health_view(health)

    if view["state"] == "none":
        return

    if view["state"] == "unavailable":
        st.subheader("Repository Health Score")
        st.warning(f"⚠️ Health score unavailable: {view['reason']}")
        return

    # Available: composite + full per-category breakdown in one result view.
    st.subheader("Repository Health Score")
    composite = view["composite"]
    st.metric("Composite score", f"{composite} / 100")

    for row in view["rows"]:
        limited = " — ⚠️ limited data" if row["limited_data"] else ""
        st.write(f"**{row['label']}**: {row['score']} / 100{limited}")

st.set_page_config(page_title="RepoPilot", page_icon="🚀")

st.title("RepoPilot")
st.write("Analyze a public GitHub repository with AI.")

owner = st.text_input("GitHub owner / organization", value="psf")
repo = st.text_input("Repository name", value="requests")

include_code = st.checkbox(
    "Include code analysis",
    value=False,
    help="Analyze source code in addition to the README for deeper insights"
)

if st.button("Analyze repository"):
    if not owner or not repo:
        st.warning("Please enter both owner and repository name.")
    else:
        with st.spinner("Analyzing repository..." + (" (including code)" if include_code else "")):
            try:
                response = requests.get(
                    f"{API_BASE_URL}/analyze",
                    params={"owner": owner, "repo": repo, "include_code": include_code},
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()

                st.subheader(f"Analysis for {data['repo']}")
                if data.get("includes_code"):
                    st.info("📝 This analysis includes code structure insights")
                st.markdown(data["analysis"])

                # Render the health score block when present (scoring enabled).
                render_health(data.get("health"))

            except requests.exceptions.RequestException as e:
                error_msg = str(e)
                try:
                    if hasattr(e, 'response') and e.response is not None:
                        error_detail = e.response.json().get('detail', error_msg)
                        error_msg = f"{error_detail}"
                except Exception:
                    pass
                st.error(f"❌ Could not analyze repository: {error_msg}")