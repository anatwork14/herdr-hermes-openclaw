"""Safely call Herdr's published CLI and keep a local explicit agent registry."""
from __future__ import annotations
import json
import re
import subprocess
import time
from pathlib import Path

from .config import KINDS, ConfigError, project_info, resolve_path, validate_agent

class RuntimeError_(RuntimeError):
    pass

def command(argv: list[str], *, timeout: int=35) -> str:
    try:
        p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError_(f"Command failed to run ({argv[0]}): {exc}") from exc
    if p.returncode:
        raise RuntimeError_(f"{argv[0]} exited {p.returncode}: {(p.stderr or p.stdout)[-1800:]}")
    return p.stdout

def herdr(cfg: dict, args: list[str], *, timeout: int=35) -> dict | str:
    output = command([cfg['herdr_binary'], *args], timeout=timeout)
    try:
        parsed = json.loads(output)
        if isinstance(parsed, dict) and parsed.get('ok') is False:
            raise RuntimeError_(f"Herdr rejected request: {parsed.get('error')}")
        return parsed
    except json.JSONDecodeError:
        return output

def nested(obj: dict | str, *keys: str) -> str:
    x = obj
    for k in keys:
        if not isinstance(x, dict) or k not in x:
            raise RuntimeError_(f"Unexpected Herdr response, missing {'.'.join(keys)}: {str(obj)[:300]}")
        x=x[k]
    if not isinstance(x,str) or not x:
        raise RuntimeError_(f"Unexpected Herdr ID: {x!r}")
    return x

def create_agent(cfg:dict, root:Path, cx, *, project:str, kind:str, name:str, yes:bool=False) -> dict:
    if not yes:
        raise ConfigError('Launching agent requires --yes (explicit owner approval)')
    validate_agent(name)
    if kind not in KINDS or kind not in cfg.get('allowed_kinds',list(KINDS)):
        raise ConfigError('Agent kind not allowed')
    info = project_info(cfg,project)
    repo=resolve_path(root, info['repo'])
    if not (repo/'.git').exists() and not repo.is_dir():
        raise RuntimeError_(f'Repository path does not exist: {repo}')
    if not repo.is_dir():
        raise RuntimeError_(f'Invalid repo directory: {repo}')
    command(['git','-C',str(repo),'rev-parse','--show-toplevel'])
    if cx.execute('SELECT 1 FROM bindings WHERE name=?',(name,)).fetchone():
        raise RuntimeError_('Agent name already bound; do not double-start')
    worktree_root = resolve_path(root,cfg['worktree_root']) / project
    worktree_root.mkdir(parents=True,exist_ok=True)
    cwd=worktree_root / name
    if cwd.exists():
        raise RuntimeError_(f'Worktree path already exists: {cwd} (recovery required; no deletion)')
    branch=f'herdr/{name}'
    # Avoid creating an orphan worktree if the Herdr server is already offline.
    herdr(cfg,['status','server'])
    command(['git','-C',str(repo),'worktree','add','-b',branch,str(cwd),'HEAD'],timeout=60)
    try:
        existing=cx.execute('SELECT * FROM bindings WHERE project=? ORDER BY created_at',(project,)).fetchone()
        if existing:
            workspace_id=existing['workspace_id']
            resp=herdr(cfg,['pane','split',existing['pane_id'],'--direction','right','--cwd',str(cwd),'--no-focus'])
            pane_id=nested(resp,'result','pane','pane_id')
        else:
            resp=herdr(cfg,['workspace','create','--cwd',str(cwd),'--label',project,'--no-focus'])
            workspace_id=nested(resp,'result','workspace','workspace_id')
            pane_id=nested(resp,'result','root_pane','pane_id')
        herdr(cfg,['agent','start',name,'--kind',kind,'--pane',pane_id,'--timeout','45000'],timeout=55)
    except Exception:
        # On failure leave worktree intact for operator recovery, avoid destructive rollback.
        raise
    with cx:
        cx.execute('INSERT INTO bindings VALUES(?,?,?,?,?,?,?)',
                   (name,project,kind,pane_id,workspace_id,str(cwd),int(time.time())))
    return dict(name=name,project=project,kind=kind,pane_id=pane_id,workspace_id=workspace_id,
                cwd=str(cwd), branch=branch)

def get_binding(cx, project:str, agent:str) -> dict:
    row=cx.execute('SELECT * FROM bindings WHERE name=? AND project=?',(agent,project)).fetchone()
    if row is None:
        raise RuntimeError_(f'Agent {agent} is not registered under project {project}; refusing cross-project control')
    return dict(row)

def prompt_agent(cfg,cx, *, project:str,agent:str,text:str,yes:bool=False,wait:bool=False) -> dict | str:
    if not yes:
        raise ConfigError('Prompting live agent requires --yes')
    binding=get_binding(cx,project,agent)
    if not text.strip() or len(text)>16000:
        raise ConfigError('Prompt length must be 1–16000')
    # Defense in depth: verify the Herdr live alias still points to bound pane.
    live=herdr(cfg,['agent','get',agent])
    if not isinstance(live, dict):
        raise RuntimeError_('Herdr agent get did not return structured JSON; refusing unsafe prompt')
    raw=json.dumps(live)
    if binding['pane_id'] not in raw:
        raise RuntimeError_('Live Herdr agent does not match registered pane; rebind manually')
    argv=['agent','prompt',agent,text]
    if wait: argv += ['--wait','--until','done','--until','blocked','--timeout','120000']
    return herdr(cfg,argv,timeout=130 if wait else 35)

def read_agent(cfg,cx, *, project:str,agent:str,lines:int=60)->dict:
    get_binding(cx,project,agent)
    if not 1<=lines<=300:
        raise ConfigError('Read limit must be 1–300 lines')
    data=herdr(cfg,['agent','read',agent,'--source','recent-unwrapped','--lines',str(lines)])
    return {'project':project,'agent':agent,'output':data if isinstance(data,str) else json.dumps(data)}

def notify(cfg:dict,project:str,text:str,*,yes:bool=False)->str:
    if not yes: raise ConfigError('Sending Discord message requires --yes')
    target=project_info(cfg,project).get('discord_channel_id')
    if not target:
        raise ConfigError(f'{project} has no Discord channel mapping')
    if not re.fullmatch(r'\d{15,22}',str(target)):
        raise ConfigError('Invalid Discord channel ID')
    if not text.strip() or len(text)>1800:
        raise ConfigError('Message must be 1–1800 characters')
    return command([cfg['openclaw_binary'],'message','send','--channel','discord',
                    '--target',f'channel:{target}','--message',text])
