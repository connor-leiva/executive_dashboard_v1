FROM python:3.12-slim
WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[postgres]" || true
COPY . .
# Install the package itself (and prod DB drivers) against the full source.
RUN pip install --no-cache-dir ".[postgres]" "uvicorn[standard]>=0.30"

EXPOSE 8000
# Bind Railway's injected $PORT (falls back to 8000 locally).
# Run migrations as the Railway pre-deploy/release command: `alembic upgrade head`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
