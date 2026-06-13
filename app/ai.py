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

def explain_repo_with_code(readme: str, code_summary: str) -> str:
    prompt = f"""
You are a senior software engineer.
Explain this GitHub project for a beginner developer, using both the README and actual code structure.

Cover:
1. What the project does (from README and code)
2. Architecture and code organization
3. Tech stack and dependencies
4. How to run it
5. Key files to understand first
6. Important patterns and design choices visible in the code

README:
{readme}

CODE STRUCTURE ANALYSIS:
{code_summary}
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return response.output_text