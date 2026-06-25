#!/usr/bin/env python
"""Reusable senpai IMPLEMENT build-script generator for the autonomous sprint loop.

Produces a standalone Workflow script that runs senpai IMPLEMENT mode with the
plan/recon inlined (so the 35KB plan never shuttles through orchestration), PLUS
an EARLY-ABORT guard: if 5 agent() calls in a row return null (the fingerprint of
a session-limit kill), it short-circuits all remaining steps so the run returns
in seconds instead of grinding ~28 min through every failing agent. That frees the
REPL so the cron / task-notification can auto-resume from the journal after reset.

Usage: python gen_build.py <senpai.js> <args.json> <run_name> <out.js>
  args.json = {"plan":{...}, "recon":{...}}  (minimal IMPLEMENT args)
"""
import json, sys

senpai_path, args_path, run_name, out_path = sys.argv[1:5]
src = open(senpai_path, encoding='utf-8').read()
a = json.load(open(args_path, encoding='utf-8'))
plan = json.dumps(a['plan'], ensure_ascii=False)
recon = json.dumps(a['recon'], ensure_ascii=False)

assert '//__PLAN_BLOCK_START__' in src and '//__PLAN_BLOCK_END__' in src, 'markers missing in senpai source'
pre, rest = src.split('//__PLAN_BLOCK_START__', 1)
_, post = rest.split('//__PLAN_BLOCK_END__', 1)
src = pre + post

# Route every agent() call through the guard. (agentType / roleToAgent use capital A -> untouched.)
src = src.replace('agent(', 'aGuarded(')

helper = (
    "//__IMPLEMENT_PLAN_INJECT__\n"
    "let __abortStreak = 0, __aborted = false\n"
    "async function aGuarded(p, o) {\n"
    "  if (__aborted) return null\n"
    "  const r = await agent(p, o)\n"
    "  if (r === null) { if (++__abortStreak >= 5) { __aborted = true; log('Early-abort: 5 consecutive agent failures (likely session limit) — short-circuiting remaining steps for a fast clean resume from journal.') } }\n"
    "  else { __abortStreak = 0 }\n"
    "  return r\n"
    "}\n"
    f"const __PLAN__ = {plan}\n"
    f"const __RECON__ = {recon}\n"
)
src = src.replace('//__IMPLEMENT_PLAN_INJECT__', helper)
src = src.replace('const plan = args.plan', 'const plan = __PLAN__')
src = src.replace(
    "const recon = args.recon || { stack: '', relevantFiles: '', patterns: '', risks: '' }",
    'const recon = __RECON__')
# break the QA loop the moment we abort, so we don't grind 5 rounds of instant-nulls
src = src.replace('while (pendingTasks.length > 0 && qaRound < 5)',
                  'while (pendingTasks.length > 0 && qaRound < 5 && !__aborted)')
# BUDGET: cap QA spin in GENERATED builds at 2 rounds (canonical senpai stays 5).
# Green code that drifts from original criteria otherwise grinds 5 rounds + expert-qa (~3M tokens);
# the orchestrator's Opus critique + green gate are the real quality bar.
src = src.replace('qaRound < 5', 'qaRound < 2')
src = src.replace('qaRound}/5', 'qaRound}/2')
src = src.replace('5 rounds of standard QA failed', '2 rounds of standard QA failed')
src = src.replace("name: 'senpai-team-v1',", f"name: '{run_name}',")

open(out_path, 'w', encoding='utf-8').write(src)
print('wrote', out_path, 'bytes', len(src))
print('aGuarded calls:', src.count('aGuarded('), '| real agent( in helper:', src.count('await agent(p, o)'),
      '| abort-guarded while:', '&& !__aborted' in src)
