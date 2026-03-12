#!/bin/sh
# Write GitHub token to git credential store so agents can push inside Docker.
# The token must be a Personal Access Token (classic) with repo scope,
# or a fine-grained token with Contents: read+write permission.
if [ -n "$GITHUB_TOKEN" ]; then
  # Detect the GitHub remote URL to build the credentials entry
  REMOTE_URL=$(git -C /app remote get-url origin 2>/dev/null || echo "")
  HOST=$(echo "$REMOTE_URL" | sed -E 's|https?://([^/]+)/.*|\1|' | sed 's/.*@//')
  if [ -z "$HOST" ]; then HOST="github.com"; fi
  printf "https://x-token-auth:%s@%s\n" "$GITHUB_TOKEN" "$HOST" > /root/.git-credentials
  echo "[entrypoint] Git credentials configured for $HOST"
else
  echo "[entrypoint] GITHUB_TOKEN not set — agents will not be able to git push"
fi

exec "$@"
