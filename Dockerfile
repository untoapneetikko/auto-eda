FROM python:3.12-slim

WORKDIR /app

# Install system deps + git + Docker CLI + Node.js 20 (required for Claude Code CLI)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    curl \
    git \
    ca-certificates \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user so Claude Code CLI accepts --dangerously-skip-permissions
RUN useradd -m -u 1000 -s /bin/bash appuser \
 && mkdir -p /home/appuser/.claude \
 && chown -R appuser:appuser /home/appuser

# Configure git for agent use: use token auth, sensible identity
RUN git config --global user.name "Auto-EDA Agent" \
 && git config --global user.email "agent@auto-eda" \
 && git config --global credential.helper store \
 && git config --global safe.directory /app \
 && git config --global safe.directory '*'

# Install Claude Code CLI (provides the claude binary used by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app

# Entrypoint: write GitHub token to git credential store if provided, then start server
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

USER appuser
ENV HOME=/home/appuser
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
