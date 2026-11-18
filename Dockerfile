# ── DriveUp ──────────────────────────────────────────────────────────────────
# One image, one process: aiogram polling + uvicorn (Studio + admin) in a
# single asyncio loop, SQLite on a mounted volume.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata

WORKDIR /app

# curl backs the healthcheck; tzdata makes timestamps readable in logs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first, so code edits don't re-download the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY driveup ./driveup
COPY main.py .
COPY scripts ./scripts

# Where SQLite and scratch files live. Render/Railway mount a volume over
# /app/data — that volume arrives owned by root, which is why the container
# starts as root and only then drops privileges (see entrypoint).
RUN mkdir -p /app/data/tmp /app/data/downloads \
 && useradd -m -u 10001 driveup \
 && chown -R driveup:driveup /app

ENV DB_PATH=/app/data/driveup.sqlite3 \
    TEMP_DIR=/app/data/tmp \
    DOWNLOAD_DIR=/app/data/downloads \
    HOST=0.0.0.0 \
    PORT=8080

COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "driveup"]
