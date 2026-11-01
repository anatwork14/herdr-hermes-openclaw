# Daily operator quick reference

Run from repository root, after bootstrap and config.

```bash
.venv/bin/herdctl doctor
.venv/bin/herdctl projects
.venv/bin/herdctl agents --project starlight-tempo
.venv/bin/herdctl read --project starlight-tempo --agent st_impl --lines 60
.venv/bin/herdctl memory context --project starlight-tempo "latest benchmark decisions"
.venv/bin/herdctl task list --project starlight-tempo
```

Change operations (need explicit approval):

```bash
.venv/bin/herdctl prompt --project starlight-tempo --agent st_impl --text "Provide current task status." --yes
.venv/bin/herdctl notify --project starlight-tempo --text "Status update" --yes
```

Never stop the Herdr server if workers are actively writing. Use `herdr` to attach and inspect; detach without terminating worker sessions. Review safety doc for recovery and sensitive permissions.
