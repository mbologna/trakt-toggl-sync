FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:ecd4de2f060c64bea0ff8ecb182ddf46ba3fcccdc8a60cfdbaf20d1a047d7437 /uv /usr/local/bin/uv

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
