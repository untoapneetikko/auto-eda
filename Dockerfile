FROM python:3.12-slim

WORKDIR /app

# Install system deps + git + Node.js 20 (required for Claude Code CLI)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    curl \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Configure git for agent use: use token auth, sensible identity
RUN git config --global user.name "Auto-EDA Agent" \
 && git config --global user.email "agent@auto-eda" \
 && git config --global credential.helper store \
 && git config --global safe.directory /app

# Install Claude Code CLI (provides the claude binary used by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Entrypoint: write GitHub token to git credential store if provided, then start server
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/backend", "--reload-dir", "/app/agents"]
