FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /repopilot

# Copy just the backend requirements file first (better layer caching)
COPY requirements-backend.txt .
RUN pip install --no-cache-dir -r requirements-backend.txt

# Copy the whole app/ package, preserving the folder structure main.py expects
COPY app/ ./app/

EXPOSE 8000

# Note: app.main:app, not main:app — matches the package structure
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]