---
name: scientific-review
description: Evidence-based research and benchmark validation, including leakage checks, baselines, ablations and reproducibility.
---
# Scientific review

Review claims against source code, datasets, configs and logs. Separate: supported, partially supported, unknown, contradicted. Check reproducibility, baselines, data leakage, metric definitions, compute budget and limitations. Do not infer GPU readiness or paper-level novelty from CPU-only tests. Require exact commit, experimental protocol, seed and output paths for every result. Present blockers and a prioritized fix list. Do not auto-run GPU experiments or data downloads without explicit authorization.
