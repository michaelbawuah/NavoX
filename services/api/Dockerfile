FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app

# Timetable screenshots are read locally; no image is uploaded to an AI provider.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY navox ./navox

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "fastapi", "run", "navox/api/main.py", "--host", "0.0.0.0", "--port", "8000"]

