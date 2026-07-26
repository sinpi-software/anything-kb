# neonews image. One image serves every role (serve / the four flows / migrate) —
# callers override the command. Build target: linux/amd64.
FROM python:3.12-slim

# uv for fast, locked installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependency layer — cached on the lockfiles. The project itself is not a
# package (no build-system), so --no-install-project installs only deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code (modules run top-level from /app).
COPY . .

CMD ["python", "serve.py"]
