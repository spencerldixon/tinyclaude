FROM python:3.14-slim

# Install Node.js (required by Claude Code CLI)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Install uv for fast Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY tinyclaude/ tinyclaude/

# Create sessions directory
RUN mkdir -p /data/sessions

ENV SESSIONS_DIR=/data/sessions
ENV CLAUDE_BIN=claude

CMD ["uv", "run", "tinyclaude"]
