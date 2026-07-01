FROM python:3.12-slim
WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[postgres]" || true
COPY . .
# Install the package itself (and prod DB drivers) against the full source.
RUN pip install --no-cache-dir ".[postgres]" "uvicorn[standard]>=0.30"

EXPOSE 8000
# Apply DB migrations on startup, then bind Railway's injected $PORT. Running
# `alembic upgrade head` here (idempotent — no-op when nothing is pending) means
# new tables/columns are always created on deploy, without a manual step.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
