#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/herdctl ]; then bash scripts/bootstrap.sh; fi
.venv/bin/python -m compileall -q src
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/herdctl doctor
