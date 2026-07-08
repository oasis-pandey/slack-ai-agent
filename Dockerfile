# Builder stage
FROM python:3.13-slim as builder

# git is needed at build time: canvas-mcp installs from a git URL (see requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Create a virtual environment so we can copy it as a single directory
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Runtime stage
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Create a non-root user
RUN useradd --create-home appuser

WORKDIR /app

# Copy the built virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the application code
COPY . .

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Socket Mode opens an outbound WebSocket — there is nothing to EXPOSE.
CMD ["python", "-m", "canvas_bot.main"]
