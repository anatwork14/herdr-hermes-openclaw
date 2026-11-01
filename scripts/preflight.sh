#!/usr/bin/env bash
# Read-only checks; never authenticates or installs third-party agents.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 --version
for executable in git herdr openclaw hermes codex claude agy; do
  if command -v "$executable" >/dev/null 2>&1; then
    printf 'FOUND    %-12s %s\n' "$executable" "$(command -v "$executable")"
  else
    printf 'MISSING  %-12s (install/auth only if required)\n' "$executable"
  fi
done
if [ -x .venv/bin/herdctl ]; then
  .venv/bin/herdctl doctor
  .venv/bin/herdctl discord-plan
else
  echo 'Run bash scripts/bootstrap.sh to provision the local Python CLI.'
fi
