#!/usr/bin/env python3
"""Link canonical skills to CLI agent skill roots without overwriting existing items.

Never mutate agent configs. Hermes/OpenClaw extraDirs are set in their own config.
"""
from pathlib import Path
import argparse

p=argparse.ArgumentParser()
p.add_argument('--apply',action='store_true',help='Actually create symbolic links')
a=p.parse_args()
root=Path(__file__).resolve().parent.parent
skills=root/'skills'/'shared'
roots=[Path.home()/'.agents'/'skills', Path.home()/'.claude'/'skills',Path.home()/'.gemini'/'config'/'skills']
for dest in roots:
    for skill in sorted(skills.iterdir()):
        if not (skill/'SKILL.md').is_file():continue
        target=dest/skill.name
        if target.exists() or target.is_symlink():
            print(f'SKIP existing {target}')
            continue
        if a.apply:
            dest.mkdir(parents=True,exist_ok=True)
            target.symlink_to(skill, target_is_directory=True)
            print(f'LINK {target} -> {skill}')
        else:
            print(f'PLAN {target} -> {skill}')
print('Hermes: configure skills.external_dirs with canonical skills/shared path (read-only recommended).')
print('OpenClaw: configure skills.load.extraDirs with canonical skills/shared path.')
