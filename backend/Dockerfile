FROM python:3.12-slim
WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[postgres]" || true
COPY . .
# Install the package itself (and prod DB drivers) against the full source.
RUN pip install --no-cache-dir ".[postgres]" "uvicorn[standard]>=0.30"

EXPOSE 8000
# Run migrations as the Railway pre-deploy/release command: `alembic upgrade head`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
