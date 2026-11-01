#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "Python 3.10+ required" >&2; exit 1; }
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python - <<'PY'
import site
from pathlib import Path
root=Path.cwd().resolve()
sites=site.getsitepackages()
if not sites:
    raise SystemExit('Unable to locate venv site-packages')
Path(sites[0], 'herdctl_local.pth').write_text(str(root/'src')+'\n')
PY
cat > .venv/bin/herdctl <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/python" -m herdctl "$@"
SH
chmod +x .venv/bin/herdctl
if [ ! -f config/projects.json ]; then cp config/projects.example.json config/projects.json; fi
if [ ! -f config/discord_layout.json ]; then cp config/discord_layout.example.json config/discord_layout.json; fi
if [ ! -f config/token_policy.json ]; then cp config/token_policy.example.json config/token_policy.json; fi
printf '\nPrepared offline-compatible herdctl. Edit config/projects.json, then run:\n'
printf '  .venv/bin/herdctl doctor\n'
printf '  .venv/bin/python -m unittest discover -s tests -v\n'
printf '\nHerdr, OpenClaw, Hermes, Codex, Claude and Antigravity must be separately installed and authenticated.\n'
