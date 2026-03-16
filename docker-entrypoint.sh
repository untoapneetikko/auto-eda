#!/bin/sh
# Configure git identity for appuser
git config --global user.name "Auto-EDA Agent"
git config --global user.email "agent@auto-eda"
git config --global credential.helper store
git config --global safe.directory '*'

# Write GitHub token to git credential store so agents can push inside Docker.
if [ -n "$GITHUB_TOKEN" ]; then
  REMOTE_URL=$(git -C /app remote get-url origin 2>/dev/null || echo "")
  # Extract host (e.g. github.com)
  HOST=$(echo "$REMOTE_URL" | sed -E 's|https?://([^/@]+@)?([^/]+)/.*|\2|')
  if [ -z "$HOST" ]; then HOST="github.com"; fi
  # Extract owner/username from remote URL path (first segment)
  OWNER=$(echo "$REMOTE_URL" | sed -E 's|https?://[^/]+/([^/]+)/.*|\1|')
  if [ -z "$OWNER" ]; then OWNER="git"; fi
  printf "https://%s:%s@%s\n" "$OWNER" "$GITHUB_TOKEN" "$HOST" > "${HOME}/.git-credentials"
  echo "[entrypoint] Git credentials configured for $OWNER@$HOST"
else
  echo "[entrypoint] GITHUB_TOKEN not set — agents will not be able to git push"
fi

exec "$@"
