# uv Python base image (project requires Python >=3.13)
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# pdf2image needs Poppler to render PDF pages
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy uv files
COPY pyproject.toml uv.lock ./

# Slow/flaky networks: default 30s download timeout is too short for the big wheels
ENV UV_HTTP_TIMEOUT=300 \
    UV_LINK_MODE=copy

# Install dependencies (including strands-agents). The cache mount keeps
# downloaded wheels between builds, so a retry after a failure doesn't start over.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# Copy agent file
COPY . .

# Expose port
EXPOSE 8080

# Run application
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

