import streamlit as st
import requests

API_BASE_URL = "http://127.0.0.1:8000"

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

            except requests.exceptions.RequestException as e:
                error_msg = str(e)
                try:
                    if hasattr(e, 'response') and e.response is not None:
                        error_detail = e.response.json().get('detail', error_msg)
                        error_msg = f"{error_detail}"
                except Exception:
                    pass
                st.error(f"❌ Could not analyze repository: {error_msg}")