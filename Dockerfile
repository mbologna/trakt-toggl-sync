FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY src/ ./src/

# Create data directory
RUN mkdir -p /app/data && chmod 700 /app/data

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Unbuffer Python output
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "src/server.py"]
