FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /build
COPY requirements.txt .
RUN uv pip install --system --no-cache --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime

COPY --from=builder /install /usr/local
RUN python -m compileall -q /usr/local/lib/python3.12

WORKDIR /app
COPY main.py server.py hoopit_client.py ./
RUN python -m compileall -q /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

USER 1001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/health', timeout=3)"]

CMD ["python", "main.py"]
