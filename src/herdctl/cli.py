"""herdctl CLI. All commands use configured project boundaries, no raw shell calls."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import ConfigError, load_config, project_info, resolve_path
from . import memory as mem
from . import discord_design
from .runtime import RuntimeError_, create_agent, get_binding, herdr, notify, prompt_agent, read_agent

def output(x):
    print(json.dumps(x,indent=2,ensure_ascii=False,default=str))

def build_parser():
    p=argparse.ArgumentParser(prog='herdctl',description='Herdr-first scoped agent and graph-memory control plane')
    p.add_argument('--config',help='Path to projects.json (or HERDCTL_CONFIG)')
    sub=p.add_subparsers(dest='domain',required=True)
    sub.add_parser('doctor')
    sub.add_parser('projects')
    sub.add_parser('discord-plan')
    b=sub.add_parser('budget',help='Show soft model-context budget for an agent role')
    b.add_argument('--role',required=True,choices=['gateway','supervisor','implementer','reviewer','explorer'])
    b.add_argument('--followup',action='store_true')
    a=sub.add_parser('project-for-channel')
    a.add_argument('channel_id',help='Trusted Discord channel/thread ID from gateway metadata')
    a.add_argument('--parent-channel-id',help='Trusted parent ID from Discord thread metadata (never message content)')
    gw=sub.add_parser('discord-gateway',help='Process trusted inbound Discord gateway message')
    gw.add_argument('--payload',help='JSON string containing message payload')
    gw.add_argument('--channel-id')
    gw.add_argument('--parent-channel-id')
    gw.add_argument('--guild-id')
    gw.add_argument('--author-id')
    gw.add_argument('--message-id')
    gw.add_argument('--content')
    gw.add_argument('--author-is-bot',action='store_true')
    gw.add_argument('--adapter-token',help='Authentication token from trusted OpenClaw gateway adapter')
    gw.add_argument('--synthetic',action='store_true',help='Explicitly mark request as synthetic test fixture')
    a=sub.add_parser('agents')
    a.add_argument('--project')
    a=sub.add_parser('launch')
    a.add_argument('--project',required=True); a.add_argument('--kind',required=True)
    a.add_argument('--name',required=True); a.add_argument('--yes',action='store_true')
    a=sub.add_parser('read')
    a.add_argument('--project',required=True); a.add_argument('--agent',required=True)
    a.add_argument('--lines',type=int,default=60)
    a=sub.add_parser('prompt')
    a.add_argument('--project',required=True); a.add_argument('--agent',required=True)
    a.add_argument('--text',required=True); a.add_argument('--yes',action='store_true')
    a.add_argument('--wait',action='store_true')
    a=sub.add_parser('notify')
    a.add_argument('--project',required=True); a.add_argument('--text',required=True)
    a.add_argument('--yes',action='store_true')
    m=sub.add_parser('memory')
    ms=m.add_subparsers(dest='operation',required=True)
    a=ms.add_parser('add')
    a.add_argument('--scope',choices=['global','project'],required=True)
    a.add_argument('--project'); a.add_argument('--type',required=True)
    a.add_argument('--subject',required=True); a.add_argument('--content',required=True)
    a.add_argument('--evidence',required=True)
    a.add_argument('--yes',action='store_true',help='Required for new global memory')
    a=ms.add_parser('retire')
    a.add_argument('--project'); a.add_argument('--id',required=True)
    a.add_argument('--status',choices=['superseded','retracted'],required=True)
    a.add_argument('--yes',action='store_true')
    a=ms.add_parser('search')
    a.add_argument('--project'); a.add_argument('--global-only',action='store_true')
    a.add_argument('query'); a.add_argument('--limit',type=int,default=8)
    a.add_argument('--status',choices=['active','superseded','retracted','all'],default='active')
    a.add_argument('--include-retired',action='store_true')
    a=ms.add_parser('link')
    a.add_argument('--project'); a.add_argument('source'); a.add_argument('relation'); a.add_argument('target')
    a=ms.add_parser('graph')
    a.add_argument('--project'); a.add_argument('id'); a.add_argument('--hops',type=int,default=2)
    a.add_argument('--status',choices=['active','superseded','retracted','all'],default='active')
    a.add_argument('--include-retired',action='store_true')
    a=ms.add_parser('context')
    a.add_argument('--project',required=True); a.add_argument('query'); a.add_argument('--budget',type=int)
    a=ms.add_parser('list')
    a.add_argument('--project'); a.add_argument('--scope',choices=['global','project'])
    t=sub.add_parser('task')
    ts=t.add_subparsers(dest='operation',required=True)
    a=ts.add_parser('set'); a.add_argument('--project',required=True)
    a.add_argument('--id',required=True); a.add_argument('--title',required=True)
    a.add_argument('--status',required=True); a.add_argument('--agent',default='')
    a.add_argument('--evidence',default='')
    a=ts.add_parser('list'); a.add_argument('--project',required=True)
    return p

def main(argv=None):
    args=build_parser().parse_args(argv)
    try:
        cfg,root=load_config(args.config)
        db=resolve_path(root,cfg['state_dir'])/'memory.sqlite3'
        cx=mem.connection(db)
        if args.domain=='doctor':
            checks={'sqlite_fts5':True, 'config_loaded':True,'db':str(db),
                    'herdr_binary_found':bool(shutil.which(cfg['herdr_binary'])),
                    'openclaw_binary_found':bool(shutil.which(cfg['openclaw_binary'])),
                    'projects':{}}
            for name,p in cfg['projects'].items():
                repo=resolve_path(root,p['repo'])
                checks['projects'][name]={'repo':str(repo),'exists':repo.exists(),
                                          'discord_mapping':bool(p.get('discord_channel_id'))}
            if checks['herdr_binary_found']:
                try:
                    checks['herdr_status']=herdr(cfg,['status','server'])
                except RuntimeError_ as exc:
                    checks['herdr_status']=str(exc)
            output(checks)
        elif args.domain=='discord-plan':
            output(discord_design.discord_plan(root,cfg))
        elif args.domain=='budget':
            output(discord_design.budget(root,args.role,args.followup))
        elif args.domain=='projects':
            output({k:{'repo':str(resolve_path(root,v['repo'])),
                       'channel':v.get('discord_channel_id','UNSET')} for k,v in cfg['projects'].items()})
        elif args.domain=='project-for-channel':
            from .discord_design import project_for_channel
            output(project_for_channel(cfg,args.channel_id,args.parent_channel_id))
        elif args.domain=='discord-gateway':
            from .discord_gateway import DiscordGateway, GatewaySecurityError
            gateway=DiscordGateway(root=root,cfg=cfg,cx=cx,adapter_token=args.adapter_token)
            if args.payload:
                payload=json.loads(args.payload)
            else:
                payload={
                    'message_id':args.message_id,
                    'guild_id':args.guild_id,
                    'channel_id':args.channel_id,
                    'parent_channel_id':args.parent_channel_id,
                    'author_id':args.author_id,
                    'content':args.content or '',
                    'author_is_bot':args.author_is_bot,
                }
            is_synthetic=bool(args.synthetic or payload.get('is_synthetic',False))
            if not is_synthetic:
                required=['message_id','guild_id','channel_id','author_id']
                missing=[f for f in required if not payload.get(f)]
                if missing:
                    raise ConfigError(f"Missing required transport metadata: {', '.join(missing)}; defaults prohibited in production ingress")
            else:
                layout=gateway.layout
                owners=layout.get('owner_user_ids') or []
                payload.setdefault('message_id','1566470000000000001')
                payload.setdefault('guild_id',layout.get('guild_id'))
                payload.setdefault('author_id',owners[0] if owners else None)
                payload['is_synthetic']=True

            adapter_token=args.adapter_token or payload.get('adapter_token')
            res=gateway.handle_message(payload,adapter_token=adapter_token,is_synthetic=is_synthetic)
            output(res)
            if not res.get('ok'):
                return 1
        elif args.domain=='agents':
            rows=cx.execute('SELECT * FROM bindings WHERE project=? ORDER BY name',(args.project,)).fetchall() if args.project else cx.execute('SELECT * FROM bindings ORDER BY project,name').fetchall()
            output([dict(x) for x in rows])
        elif args.domain=='launch':
            output(create_agent(cfg,root,cx,project=args.project,kind=args.kind,name=args.name,yes=args.yes))
        elif args.domain=='prompt':
            output(prompt_agent(cfg,cx,project=args.project,agent=args.agent,text=args.text,yes=args.yes,wait=args.wait))
        elif args.domain=='read':
            output(read_agent(cfg,cx,project=args.project,agent=args.agent,lines=args.lines))
        elif args.domain=='notify':
            output({'result':notify(cfg,args.project,args.text,yes=args.yes)})
        elif args.domain=='memory':
            if args.operation in ('add','retire','search','link','graph','context','list') and args.project:
                project_info(cfg,args.project)
            if args.operation=='add':
                if args.scope=='global' and not args.yes:
                    raise ConfigError('Global memory write requires --yes and owner authorization')
                output({'id':mem.add(cx,scope=args.scope,project=args.project,type=args.type,
                     subject=args.subject,content=args.content,evidence=args.evidence)})
            elif args.operation=='retire':
                if not args.yes: raise ConfigError('Retiring memory requires --yes')
                try:
                    mem.retire(cx, args.id, args.status, project=args.project)
                except mem.MemoryError as exc:
                    raise ConfigError(str(exc)) from exc
                output({'ok':True,'id':args.id,'status':args.status})
            elif args.operation=='search':
                output(mem.search(cx,args.query,project=args.project,limit=args.limit,
                                  global_only=args.global_only,status=args.status,
                                  include_retired=args.include_retired))
            elif args.operation=='link':
                mem.link(cx,args.source,args.relation,args.target,args.project)
                output({'ok':True})
            elif args.operation=='graph':
                output(mem.graph(cx,args.id,project=args.project,hops=args.hops,
                                 status=args.status,include_retired=args.include_retired))
            elif args.operation=='context':
                output(mem.context(cx,project=args.project,query=args.query,budget=args.budget or cfg['default_memory_budget']))
            elif args.operation=='list':
                if args.scope == 'project' and not args.project:
                    raise ConfigError('--scope project requires --project')
                rows=cx.execute("SELECT * FROM memories WHERE status='active' AND (scope='global' OR (scope='project' AND project=?)) ORDER BY updated_at DESC LIMIT 100",(args.project,)).fetchall()
                output([dict(r) for r in rows if not args.scope or r['scope']==args.scope])
        elif args.domain=='task':
            project_info(cfg,args.project)
            if args.operation=='set':
                mem.task_upsert(cx,args.id,args.project,args.title,args.status,args.agent,args.evidence)
                output({'ok':True,'task_id':args.id})
            else:
                output([dict(r) for r in cx.execute('SELECT * FROM task_state WHERE project=? ORDER BY updated_at DESC',(args.project,)).fetchall()])
    except (ConfigError,mem.MemoryError,RuntimeError_,ValueError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
