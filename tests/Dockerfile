FROM python:3.11-slim

WORKDIR /app

# Install test dependencies (no GPU, no torch — pure logic tests)
COPY tests/requirements-test.txt /app/tests/
RUN pip install --no-cache-dir -r /app/tests/requirements-test.txt

# Copy project code
COPY cortex_on/ /app/cortex_on/
COPY tests/ /app/tests/

# Run tests
CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "--junitxml=/app/tests/results/report.xml"]
