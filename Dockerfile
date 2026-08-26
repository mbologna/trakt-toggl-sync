FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /usr/local/bin/uv

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
