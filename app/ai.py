from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()  #loading .env file

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def explain_repo(readme: str) -> str:
    prompt = f"""
You are a senior software engineer.
Explain this GitHub project for a beginner developer.

Cover:
1. What the project does
2. Tech stack
3. How to run it
4. What files or concepts to inspect first

README:
{readme}
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return response.output_text