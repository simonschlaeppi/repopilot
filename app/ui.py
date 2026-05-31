import streamlit as st
import requests

API_BASE_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="RepoPilot", page_icon="🚀")

st.title("🚀 RepoPilot")
st.write("Analyze a public GitHub repository with AI.")

owner = st.text_input("GitHub owner / organization", value="psf")
repo = st.text_input("Repository name", value="requests")

if st.button("Analyze repository"):
    if not owner or not repo:
        st.warning("Please enter both owner and repository name.")
    else:
        with st.spinner("Analyzing repository..."):
            try:
                response = requests.get(
                    f"{API_BASE_URL}/analyze",
                    params={"owner": owner, "repo": repo},
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()

                st.subheader(f"Analysis for {data['repo']}")
                st.markdown(data["analysis"])

            except requests.exceptions.RequestException as e:
                st.error(f"Could not analyze repository: {e}")