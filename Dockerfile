FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "scripts/start_app.py"]
