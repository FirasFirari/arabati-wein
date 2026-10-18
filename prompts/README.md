# Subagent prompts — copied out of the session scratchpad 2026-08-24

The scratchpad is per-session and disappears. These are the prompts the
local (no-API) pipeline runs on. `OPERATIONS.md` explains the workflow.

| file | used for |
|---|---|
| `analyst_system_prompt.txt` | the project's real analyst instruction set, generated from `carhunt/analyzer.py` via `_build_system(cfg['car'])`. Regenerate it if the car profile or the alias/body-type rules change. |
| `backlog_prompt.txt` | clearing new intake — points agents at `data/local_batches`, writes `data/local_verdicts` |
| `reextract_prompt.txt` | re-reading the 582 under-extracted posts — `data/reextract_batches` -> `data/reextract_verdicts`. Stresses that COMPLETENESS is the task. |
| `image_sweep_prompt.txt` | vision pass over post images: classify and transcribe only genuinely legible identifiers |

Two things every dispatch must include, both learned the hard way:

1. **An explicit anti-fabrication paragraph**, naming the real incidents (six
   invented vehicle records; a phone number carried across posts onto 117
   vehicles). Agents that got this paragraph started running their own
   grounding checks and caught their own errors before writing.
2. **A distinct output subdirectory per agent.** When several agents shared
   one directory their forks overwrote each other's files three separate
   times. The work survived only because a reconciliation pass caught it.

Regenerate the analyst prompt with:

    python -c "from carhunt.config import load_config; from carhunt.analyzer import _build_system; \
      open('prompts/analyst_system_prompt.txt','w',encoding='utf-8').write(_build_system(load_config()['car']))"
