# Canvas Slack Agent — runs as a long-lived Socket Mode worker (no inbound port).
FROM python:3.13-slim

# git is needed at build time: canvas-mcp installs from a git URL (see requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install deps first so the layer caches across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# canvas-mcp installs a `canvas-mcp-server` console script onto PATH; the bot
# spawns it as a stdio subprocess at runtime.
COPY . .

# Socket Mode opens an outbound WebSocket — there is nothing to EXPOSE.
CMD ["python", "app.py"]
