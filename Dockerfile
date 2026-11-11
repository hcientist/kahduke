# Based on the course's Dockerfile.example
# (https://github.com/CS-347-F26/google-cloud). Image tag matches .python-version.

FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    AUTOBAHN_USE_NVX=0

# AUTOBAHN_USE_NVX=0: Daphne's autobahn has no Linux wheel for Python 3.15 yet, and
# its source build otherwise tries to compile an optional SIMD extension
# (there is no compiler in this slim image). The pure-Python build is fine.

# Dependencies first, so editing your own code does not reinstall Django.
# --locked fails loudly if uv.lock disagrees with pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group prod

COPY . .

# Runs at build time against the SQLite default; no database is reachable
# here and collectstatic doesn't need one. WhiteNoise serves the result.
# REDIS_URL is optional in settings.py, so no placeholder is needed.
RUN python manage.py collectstatic --noinput

# Migrate on start (safe to repeat), then serve HTTP and WebSockets with Daphne.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec daphne -b 0.0.0.0 -p 8000 kahduke_config.asgi:application"]
