---
name: memory-recall
description: Retrieve and save evidence-backed global/project memory with token budgets and strict scope.
---
# Memory recall

Memory retrieval is opt-in and targeted, not a dump of history. First formulate a precise query:
`herdctl memory context --project <project> --budget 1600 "<keywords>"`

Read repository evidence when a memory conflicts with current files; source files and validated experiments win. When an important decision is confirmed, store it with a permanent evidence pointer:
`herdctl memory add --scope project --project <project> --type decision --subject "..." --content "..." --evidence "git:<sha>"`

Global memory is for durable shared procedures and preferences only; promotion from project to global requires explicit owner authorization. Do not store credentials, entire transcripts, sensitive information, or speculative claims as verified facts. Memory has `active`, `superseded`, and `retracted` statuses. Cite IDs and evidence in handoffs.
