FROM python:3.12-slim

WORKDIR /app

# Install system deps + Node.js 20 (required for Claude Code CLI)
RUN apt-get update && apt-get install -y \
    poppler-utils \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI (provides the claude binary used by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/backend", "--reload-dir", "/app/agents"]
