# agentic_browser/Dockerfile  (or ta-browser/Dockerfile)
FROM mcr.microsoft.com/playwright/python:v1.41.2-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Playwright browsers are already handled well in this image,
# but installing chromium explicitly is fine
RUN playwright install chromium

#CMD ["python", "main.py"]
CMD ["uvicorn", "core.server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "10"]

