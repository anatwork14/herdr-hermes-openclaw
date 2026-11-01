#!/usr/bin/env python3
"""Link canonical skills to CLI agent skill roots without overwriting existing items.

Never mutate agent configs. Hermes/OpenClaw extraDirs are set in their own config.
"""
from pathlib import Path
import argparse
import sys

root=Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / 'src'))

from herdctl.skills import install_symlinks

p=argparse.ArgumentParser()
p.add_argument('--apply',action='store_true',help='Actually create symbolic links')
a=p.parse_args()
skills=root/'skills'/'shared'
roots=[Path.home()/'.agents'/'skills', Path.home()/'.claude'/'skills',Path.home()/'.gemini'/'config'/'skills']

results = install_symlinks(skills, roots, apply=a.apply)
for r in results:
    if r['action'] == 'SKIP':
        print(f"SKIP existing {r['target']}")
    elif a.apply:
        print(f"LINK {r['target']} -> {r['source']}")
    else:
        print(f"PLAN {r['target']} -> {r['source']}")

print('Hermes: configure skills.external_dirs with canonical skills/shared path (read-only recommended).')
print('OpenClaw: configure skills.load.extraDirs with canonical skills/shared path.')
