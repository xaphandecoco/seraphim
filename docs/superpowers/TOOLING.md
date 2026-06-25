# Sprint-Loop Tooling — Recreate for the Next Agent

The autonomous sprint loop (see [`HANDOFF.md`](./HANDOFF.md) + [`SPRINT-LOOP-LOG.md`](./SPRINT-LOOP-LOG.md))
depends on two pieces of tooling that live **outside the repo** (user-level config + a generator script),
so they are NOT in normal source control. This file reproduces both verbatim so any agent/environment can
recreate them.

## What they are
1. **`senpai-team-v1.js`** — the engineering pipeline Workflow, split **PLAN → (orchestrator approves) → IMPLEMENT**
   so the human gate is real (a background workflow can't pause for the user). Performs **no git mutations**.
   Goes in **`~/.claude/workflows/senpai-team-v1.js`**. Invoked via the Workflow tool as `name: 'senpai-team-v1'`.
2. **`gen_build.py`** — generates a standalone IMPLEMENT-only Workflow script from the live senpai source with the
   approved plan/recon **inlined** (so the plan never shuttles through orchestration context), plus an **early-abort**
   guard (returns in seconds on a session-limit kill) and a **2-round QA cap** (canonical senpai stays 5; this is a
   budget protection for the loop). Put it anywhere (e.g. a `tools/` dir or temp).

## Dependencies / gotchas (required for the tooling to work)
- **17 subagent definitions** in `~/.claude/agents/*.md`, named `senpai-team-v1-{intake, recon, researcher,
  architect, backend, frontend, db, aiml, qa, qa-expert, security, ux-reviewer, performance, devops, rollback,
  docs, classifier}`. The workflow's `agentType:` calls won't resolve without them. (Ask the prior session to dump
  these too if you don't have them.)
- The marker comments **`//__PLAN_BLOCK_START__`**, **`//__PLAN_BLOCK_END__`**, **`//__IMPLEMENT_PLAN_INJECT__`**
  in `senpai-team-v1.js` are **required** — `gen_build.py` splits on them. Do not remove.
- Model policy: senpai keeps its own models (Opus on architect/security/qa-expert + the explicit overrides). Any
  agent summoned **outside** this pipeline (the critic, helpers) should use Sonnet; **the critique itself = Opus**.

## How they fit the per-sprint loop
1. Orchestrator runs senpai **PLAN** (`Workflow({name:'senpai-team-v1', args:'<spec-seed string incl. anti-spin
   migration policy>'})`) → returns `{plan, recon, openQuestions}`.
2. Orchestrator reviews open questions (logs owner FYIs), then writes a minimal `args.json` = `{"plan":{…},"recon":{…}}`
   and runs `python gen_build.py <senpai.js> <args.json> <run_name> <out.js>`.
3. Orchestrator runs the generated build: `Workflow({scriptPath:'<out.js>'})`. If it dies on the session limit,
   **resume from journal**: `Workflow({scriptPath:'<out.js>', resumeFromRunId:'<runId>'})`.
4. Orchestrator runs the green gate + a single **Opus** critic agent (grades the diff vs. the DoD), then commits.

---

## 1. `~/.claude/workflows/senpai-team-v1.js`

```javascript
export const meta = {
  name: 'senpai-team-v1',
  description: 'Engineering pipeline (split for real human gates): PLAN mode (Intake → Recon → Architect) returns a plan for your approval; IMPLEMENT mode (Engineers → QA Loop → Security → DevOps → Docs) runs the approved plan.',
  phases: [
    { title: 'Intake',          detail: 'Parse spec and surface ambiguities (plan mode)' },
    { title: 'Recon',           detail: 'Map codebase and identify risks (plan mode)' },
    { title: 'Research',        detail: 'Investigate unfamiliar tech (conditional, plan mode)' },
    { title: 'Architecture',    detail: 'Design plan, return for approval (Opus 4.8, plan mode)' },
    { title: 'Implementation',  detail: 'Feature engineers + patch batch in parallel (implement mode)' },
    { title: 'QA',              detail: 'Up to 5 rounds; Expert QA escalation on round 6 (Opus 4.8)' },
    { title: 'Review',          detail: 'Security (Opus 4.8) + UX + Performance' },
    { title: 'DevOps',          detail: 'Deployment verification + rollback planning' },
    { title: 'Docs & PR',       detail: 'Sprint digest + PR text (no git mutations)' }
  ]
}

// ── Execution model ───────────────────────────────────────────────────────────
// This workflow runs in the BACKGROUND, so a subagent cannot pause to ask the
// user and wait for a reply. The human gates (intake clarifications + plan
// approval) are therefore handled by the ORCHESTRATOR (main loop) between two
// invocations:
//   1. PLAN mode  — args is the spec string / null / {mode:'plan', spec, planFeedback}.
//                   Runs Intake → Recon → Research → Architect, returns the plan.
//   2. IMPLEMENT mode — args is {mode:'implement', plan, recon}.
//                   Runs Implementation → QA → Review → DevOps → Docs.
// The orchestrator shows the returned plan to the user, collects approval or
// CHANGES feedback (re-invoking PLAN mode with planFeedback to revise), and only
// then invokes IMPLEMENT mode with the approved plan.
//
// MODEL POLICY: the senpai team keeps its own per-agent models — the key
// reviewer/architect roles stay on Opus 4.8 (explicit overrides below); the rest
// use their agent-definition defaults. (Sonnet-only is enforced for agents
// spawned OUTSIDE this pipeline, not here.)

// ── Schemas ─────────────────────────────────────────────────────────────────

const INTAKE_SCHEMA = {
  type: 'object',
  properties: {
    featureTasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name:               { type: 'string' },
          goal:               { type: 'string' },
          role:               { type: 'string' },
          acceptanceCriteria: { type: 'string' },
          constraints:        { type: 'string' }
        },
        required: ['name', 'goal', 'role', 'acceptanceCriteria']
      }
    },
    patchTasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          goal: { type: 'string' },
          role: { type: 'string' }
        },
        required: ['name', 'goal', 'role']
      }
    },
    openQuestions: { type: 'string' }
  },
  required: ['featureTasks', 'patchTasks']
}

const RECON_SCHEMA = {
  type: 'object',
  properties: {
    stack:              { type: 'string' },
    relevantFiles:      { type: 'string' },
    patterns:           { type: 'string' },
    risks:              { type: 'string' },
    hasUnfamiliarTech:  { type: 'boolean' },
    unfamiliarItems:    { type: 'string' }
  },
  required: ['stack', 'relevantFiles', 'risks', 'hasUnfamiliarTech']
}

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    planSummary: { type: 'string' },
    featureTasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name:               { type: 'string' },
          role:               { type: 'string' },
          acceptanceCriteria: { type: 'string' },
          filesToChange:      { type: 'string' },
          engineerNotes:      { type: 'string' },
          dependsOn:          { type: 'string' }
        },
        required: ['name', 'role', 'acceptanceCriteria', 'filesToChange']
      }
    },
    patchTasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          goal: { type: 'string' },
          role: { type: 'string' }
        },
        required: ['name', 'goal', 'role']
      }
    },
    hasFrontendChanges:  { type: 'boolean' },
    hasDbOrDataHeavyOps: { type: 'boolean' },
    hasMigrations:       { type: 'boolean' },
    risks:               { type: 'string' }
  },
  required: ['planSummary', 'featureTasks', 'patchTasks', 'hasFrontendChanges', 'hasDbOrDataHeavyOps', 'hasMigrations']
}

const QA_SCHEMA = {
  type: 'object',
  properties: {
    taskName:        { type: 'string' },
    verdict:         { type: 'string', enum: ['PASS', 'FAIL'] },
    criteriaChecked: { type: 'string' },
    failures:        { type: 'string' },
    fixGuidance:     { type: 'string' }
  },
  required: ['taskName', 'verdict']
}

const SECURITY_SCHEMA = {
  type: 'object',
  properties: {
    verdict:     { type: 'string', enum: ['PASS', 'BLOCK'] },
    findings:    { type: 'string' },
    lowFindings: { type: 'string' }
  },
  required: ['verdict']
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function roleToAgent(role) {
  if (role === 'frontend') return 'senpai-team-v1-frontend'
  if (role === 'db')       return 'senpai-team-v1-db'
  if (role === 'aiml')     return 'senpai-team-v1-aiml'
  return 'senpai-team-v1-backend'
}

function slug(name) {
  return (name || 'task').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 40)
}

function buildPlanText(rawPlan) {
  const featureLines = rawPlan.featureTasks.map(t =>
    `  • [${t.role}] ${t.name}\n    Criteria: ${t.acceptanceCriteria}\n    Files: ${t.filesToChange}${t.dependsOn ? '\n    Depends on: ' + t.dependsOn : ''}`
  ).join('\n')

  const patchLines = rawPlan.patchTasks.length
    ? rawPlan.patchTasks.map(t => `  • ${t.name}: ${t.goal}`).join('\n')
    : '  (none)'

  return (
    `── PLAN ─────────────────────────────────────────\n\n` +
    `${rawPlan.planSummary}\n\n` +
    `FEATURE TASKS (full pipeline):\n${featureLines}\n\n` +
    `PATCH TASKS (batch route):\n${patchLines}\n\n` +
    `Risks: ${rawPlan.risks || 'none'}\n` +
    `Flags: frontend=${rawPlan.hasFrontendChanges}, db/data=${rawPlan.hasDbOrDataHeavyOps}, migrations=${rawPlan.hasMigrations}\n` +
    `─────────────────────────────────────────────────`
  )
}

// ── Mode detection ────────────────────────────────────────────────────────────

const isObjectArgs = args && typeof args === 'object'
const MODE = isObjectArgs && args.mode === 'implement' ? 'implement' : 'plan'

// ══════════════════════════════════════════════════════════════════════════════
// PLAN MODE — Intake → Recon → Research → Architect → return plan for approval
// ══════════════════════════════════════════════════════════════════════════════

//__PLAN_BLOCK_START__
if (MODE === 'plan') {
  const specSource = isObjectArgs ? args.spec : args
  const planFeedback = isObjectArgs && args.planFeedback ? String(args.planFeedback) : ''

  const spec = specSource
    ? (typeof specSource === 'string' ? specSource : JSON.stringify(specSource))
    : 'No spec provided. Check the project root for SPEC.md, CLAUDE.md, or any *.spec.md files and read them.'

  // ── Intake (reuse prior intake on a revision pass to save a round) ───────────
  let intake = isObjectArgs && args.intake ? args.intake : null
  if (!intake) {
    phase('Intake')
    log('Intake Agent reading spec...')
    intake = await agent(
      `SPEC:\n${spec}\n\nRead the spec carefully (read any referenced files). Extract every distinct task. Classify each:\n- FEATURE: >3 files, schema change, new feature, or multi-role\n- PATCH: ≤3 files, no schema change, single role, fixing existing behavior\n\nThis runs in a background pipeline: you CANNOT pause to ask the user. If anything is ambiguous, make the most reasonable assumption, proceed, and record every assumption and unresolved question in the "openQuestions" field so the orchestrator can raise them with the user before implementation.`,
      { schema: INTAKE_SCHEMA, label: 'intake', phase: 'Intake', agentType: 'senpai-team-v1-intake' }
    )
    if (!intake) return { error: 'Intake failed or was cancelled.' }
    log(`Intake: ${intake.featureTasks.length} feature task(s), ${intake.patchTasks.length} patch task(s)`)
  } else {
    log('Reusing intake from previous plan pass.')
  }

  // ── Recon (reuse prior recon on a revision pass) ─────────────────────────────
  let recon = isObjectArgs && args.recon ? args.recon : null
  if (!recon) {
    phase('Recon')
    const taskList = [
      ...intake.featureTasks.map(t => `[FEATURE] ${t.name}: ${t.goal}`),
      ...intake.patchTasks.map(t =>   `[PATCH]   ${t.name}: ${t.goal}`)
    ].join('\n')
    log('Recon mapping codebase...')
    recon = await agent(
      `TASKS:\n${taskList}\n\nMap relevant files per task. Detect tech stack. Note existing patterns (naming conventions, error handling, auth approach, folder structure). Flag risks: breaking changes, missing migrations, shared utilities that could regress, missing env vars. Flag any unfamiliar libraries or external APIs not found in the codebase.`,
      { schema: RECON_SCHEMA, label: 'recon', phase: 'Recon', agentType: 'senpai-team-v1-recon' }
    )
    if (!recon) return { error: 'Recon failed.' }
  } else {
    log('Reusing recon from previous plan pass.')
  }

  // ── Research (conditional) ───────────────────────────────────────────────────
  let research = isObjectArgs && args.research ? String(args.research) : ''
  if (!research && recon.hasUnfamiliarTech && recon.unfamiliarItems) {
    phase('Research')
    log(`Researching unfamiliar tech: ${recon.unfamiliarItems}`)
    research = await agent(
      `Recon flagged these unfamiliar items:\n${recon.unfamiliarItems}\n\nFor each: recommended approach, key facts, gotchas, minimal code example, confidence level (HIGH/MEDIUM/LOW). Use WebSearch for official docs from the last 2 years.`,
      { label: 'researcher', phase: 'Research', agentType: 'senpai-team-v1-researcher' }
    ) || ''
  }

  // ── Architect (Opus 4.8) ─────────────────────────────────────────────────────
  phase('Architecture')
  log(`Architect designing plan (Opus 4.8)${planFeedback ? ' — revising per user feedback' : ''}...`)

  const rawPlan = await agent(
    `INTAKE OUTPUT:\nFeature tasks: ${JSON.stringify(intake.featureTasks, null, 2)}\nPatch tasks: ${JSON.stringify(intake.patchTasks, null, 2)}\n\nRECON OUTPUT:\nStack: ${recon.stack}\nRelevant files: ${recon.relevantFiles}\nPatterns: ${recon.patterns}\nRisks: ${recon.risks}${research ? '\n\nRESEARCH FINDINGS:\n' + research : ''}${planFeedback ? '\n\nUSER FEEDBACK — address this in the revised plan:\n' + planFeedback : ''}\n\nProduce a full implementation plan. For each feature task: assign role (backend/frontend/db/aiml), specific testable acceptance criteria, exact files to change, engineer notes (patterns to follow, gotchas), and dependsOn if it requires another task first (especially DB tasks). Confirm patch tasks for batch routing. Set hasFrontendChanges, hasDbOrDataHeavyOps, hasMigrations flags accurately.`,
    { schema: PLAN_SCHEMA, label: 'architect', phase: 'Architecture', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-architect' }
  )
  if (!rawPlan) return { error: 'Architect failed.' }

  const planText = buildPlanText(rawPlan)
  log(planText)

  // Return everything the orchestrator needs to (a) show the user the plan +
  // open questions, and (b) re-invoke this workflow — either PLAN mode with
  // planFeedback to revise, or IMPLEMENT mode once approved.
  return {
    mode: 'plan',
    needsApproval: true,
    planText,
    plan: rawPlan,
    openQuestions: intake.openQuestions || '',
    // pass-through context so a revision or implement call need not re-run intake/recon
    intake,
    recon: { stack: recon.stack, relevantFiles: recon.relevantFiles, patterns: recon.patterns, risks: recon.risks },
    research,
    instructions: 'Show planText + openQuestions to the user. To revise: re-invoke with args {mode:"plan", spec, planFeedback, intake, recon, research}. To build: re-invoke with args {mode:"implement", plan, recon}.'
  }
}

//__PLAN_BLOCK_END__

// ══════════════════════════════════════════════════════════════════════════════
// IMPLEMENT MODE — approved plan → Engineers → QA → Review → DevOps → Docs
// ══════════════════════════════════════════════════════════════════════════════

//__IMPLEMENT_PLAN_INJECT__
const plan = args.plan
if (!plan || !Array.isArray(plan.featureTasks)) {
  return { error: 'IMPLEMENT mode requires args.plan (the approved plan object returned by PLAN mode).' }
}
const recon = args.recon || { stack: '', relevantFiles: '', patterns: '', risks: '' }
const reconCtx = `Stack: ${recon.stack}\nRelevant files: ${recon.relevantFiles}\nPatterns to follow: ${recon.patterns}`

// ── Phase 3: Implementation (parallel branches) ──────────────────────────────

phase('Implementation')

// DB tasks run first — other engineers may depend on their schema output
const dbTasks    = plan.featureTasks.filter(t => t.role === 'db')
const nonDbTasks = plan.featureTasks.filter(t => t.role !== 'db')

const dbOutputMap = {}
if (dbTasks.length > 0) {
  log(`Running ${dbTasks.length} DB task(s) first (schema may block other engineers)...`)
  const dbResults = await parallel(dbTasks.map(task => () =>
    agent(
      `TASK: ${task.name}\nACCEPTANCE CRITERIA: ${task.acceptanceCriteria}\nFILES TO CHANGE: ${task.filesToChange}\nNOTES: ${task.engineerNotes || 'none'}\n\nCODEBASE CONTEXT:\n${reconCtx}`,
      { label: `db-${slug(task.name)}`, phase: 'Implementation', agentType: 'senpai-team-v1-db' }
    )
  ))
  dbTasks.forEach((t, i) => { dbOutputMap[t.name] = dbResults[i] || '' })
}

// All other feature tasks run in parallel
log(`Running ${nonDbTasks.length} feature engineer(s) in parallel (Branch A)...`)
if (nonDbTasks.length > 0) {
  await parallel(nonDbTasks.map(task => () => {
    const dbDep = task.dependsOn && dbOutputMap[task.dependsOn]
      ? `\nDB SCHEMA OUTPUT (from DB specialist for "${task.dependsOn}"):\n${dbOutputMap[task.dependsOn]}`
      : ''
    return agent(
      `TASK: ${task.name}\nACCEPTANCE CRITERIA: ${task.acceptanceCriteria}\nFILES TO CHANGE: ${task.filesToChange}\nNOTES: ${task.engineerNotes || 'none'}${dbDep}\n\nCODEBASE CONTEXT:\n${reconCtx}`,
      { label: `eng-${slug(task.name)}`, phase: 'Implementation', agentType: roleToAgent(task.role) }
    )
  }))
}

// Branch B: patch tasks inline (parallel engineers + 1-round QA, no Architect)
const patchQAResults = []
if (plan.patchTasks.length > 0) {
  log(`Branch B: ${plan.patchTasks.length} patch task(s) running in parallel...`)
  const patchEngineers = await parallel(plan.patchTasks.map(task => () =>
    agent(
      `PATCH TASK: ${task.name}\nGOAL: ${task.goal}\nMinimal change only — no refactors, no new features beyond scope.\n\nCODEBASE CONTEXT:\n${reconCtx}`,
      { label: `patch-${slug(task.name)}`, phase: 'Implementation', agentType: roleToAgent(task.role) }
    )
  ))

  log(`QA for ${plan.patchTasks.length} patch task(s) (1 round, no loop)...`)
  const patchQA = await parallel(plan.patchTasks.map((task, i) => () =>
    agent(
      `QA for patch: ${task.name}\nGoal: ${task.goal}\nEngineer output: ${patchEngineers[i] || '(no output)'}\nOne round only — no loop. Read the actual files. PASS or FAIL with specific reason. A failed patch is skipped, not retried.`,
      { schema: QA_SCHEMA, label: `qa-patch-${slug(task.name)}`, phase: 'Implementation', agentType: 'senpai-team-v1-qa' }
    )
  ))
  patchQAResults.push(...(patchQA || []).filter(Boolean))
  const pPass = patchQAResults.filter(r => r.verdict === 'PASS').length
  log(`Patch QA: ${pPass}/${plan.patchTasks.length} passed`)
}

// ── Phase 4: QA Loop — Branch A only (max 5 rounds, Opus escalation) ─────────

phase('QA')

let qaRound = 0
let pendingTasks = [...plan.featureTasks]
const passedTaskNames = []

while (pendingTasks.length > 0 && qaRound < 5) {
  qaRound++
  log(`QA round ${qaRound}/5 — ${pendingTasks.length} task(s)...`)

  const qaResults = await parallel(pendingTasks.map(task => () =>
    agent(
      `QA CHECK — Task: ${task.name}\nACCEPTANCE CRITERIA: ${task.acceptanceCriteria}\nRound ${qaRound} of max 5. Read the actual files on disk. Validate every acceptance criterion. Write missing tests for critical logic. PASS only if ALL criteria met with no regressions introduced.`,
      { schema: QA_SCHEMA, label: `qa-${slug(task.name)}-r${qaRound}`, phase: 'QA', agentType: 'senpai-team-v1-qa' }
    )
  ))

  const results  = (qaResults || []).filter(Boolean)
  const passes   = results.filter(r => r.verdict === 'PASS')
  const failures = results.filter(r => r.verdict === 'FAIL')

  passes.forEach(r => passedTaskNames.push(r.taskName))
  log(`Round ${qaRound}: ${passes.length} passed, ${failures.length} failed`)

  if (failures.length === 0) break

  if (qaRound < 5) {
    log(`Routing ${failures.length} failure(s) through Architect (Opus 4.8)...`)
    const reRoute = await agent(
      `QA round ${qaRound} produced these failures. Produce specific fix instructions for each engineer:\n\n${failures.map(f => `Task: ${f.taskName}\nFailures: ${f.failures || 'unspecified'}\nFix guidance needed: ${f.fixGuidance || 'see failures'}`).join('\n---\n')}`,
      { label: `reroute-r${qaRound}`, phase: 'QA', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-architect' }
    )

    log(`Re-running ${failures.length} engineer(s) with fix guidance...`)
    await parallel(failures.map(f => () => {
      const planTask = plan.featureTasks.find(t => t.name === f.taskName)
      if (!planTask) return Promise.resolve(null)
      return agent(
        `FIX QA FAILURES\nTask: ${f.taskName}\nFailures from QA: ${f.failures || 'see fix guidance'}\nFix guidance: ${f.fixGuidance || 'none'}\nArchitect re-routing instructions: ${reRoute || 'none'}\n\nAcceptance criteria to satisfy: ${planTask.acceptanceCriteria}\nFiles: ${planTask.filesToChange}`,
        { label: `fix-${slug(f.taskName)}-r${qaRound}`, phase: 'QA', agentType: roleToAgent(planTask.role) }
      )
    }))

    pendingTasks = failures
      .map(f => plan.featureTasks.find(t => t.name === f.taskName))
      .filter(Boolean)
  }
}

// Expert QA escalation if round 5 still failed
if (pendingTasks.length > 0) {
  log(`Escalating to Expert QA (Opus 4.8) after ${qaRound} failed rounds...`)
  const expertQa = await agent(
    `5 rounds of standard QA failed. Do NOT re-run the same checks — find WHY.\n\nPersistently failing tasks:\n${pendingTasks.map(t => `Task: ${t.name}\nCriteria: ${t.acceptanceCriteria}`).join('\n---\n')}\n\nTrace the failure. Root cause: logic bug, misunderstood requirement, data issue, flawed test, or wrong assumption? Verdict: ESCALATE TO USER or FIXABLE (with exact instructions).`,
    { label: 'expert-qa', phase: 'QA', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-qa-expert' }
  )
  if (!expertQa || expertQa.includes('ESCALATE TO USER')) {
    log('DELIVERY BLOCKED — QA failed after Expert QA escalation. Review findings above.')
    return { blocked: 'QA after 6 rounds', failedTasks: pendingTasks.map(t => t.name), expertQaReport: expertQa }
  }
}

// ── Phase 5: Review Gates (parallel) ─────────────────────────────────────────

phase('Review')

const reviewAgents = [
  () => agent(
    `Security audit every changed file in the sprint. OWASP Top 10: auth gates, authorization boundaries, input validation, SQL/command injection, XSS, IDOR, rate limiting, sensitive data in logs or errors, session management. Read each file. Challenge assumptions. PASS or BLOCK.`,
    { schema: SECURITY_SCHEMA, label: 'security', phase: 'Review', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-security' }
  )
]

if (plan.hasFrontendChanges) {
  reviewAgents.push(() => agent(
    `UX review of all frontend changes. Check: usability (clear actions, actionable error messages, feedback on interactions), accessibility (semantic HTML, ARIA labels, keyboard navigation, no color-only state indicators), UI consistency with existing patterns, loading/error/empty states for every async op. Advise only — do not block delivery.`,
    { label: 'ux', phase: 'Review', agentType: 'senpai-team-v1-ux-reviewer' }
  ))
}

if (plan.hasDbOrDataHeavyOps) {
  reviewAgents.push(() => agent(
    `Performance review of DB and data-heavy changes. Check: N+1 queries, missing indexes on WHERE/JOIN/ORDER BY columns, unbounded queries without pagination, sync ops that should be async, repeated DB calls that could be cached. Frontend: new dependencies over 50KB, unnecessary re-renders, blocking main thread ops. Advise only — do not block delivery.`,
    { label: 'perf', phase: 'Review', agentType: 'senpai-team-v1-performance' }
  ))
}

const reviewResults = await parallel(reviewAgents)
const securityResult = (reviewResults || [])[0]

if (securityResult && securityResult.verdict === 'BLOCK') {
  log('Security BLOCKED delivery. Routing findings through Architect (Opus 4.8)...')
  const secRoute = await agent(
    `Security blocked delivery with these findings:\n${securityResult.findings}\n\nRoute each finding to the appropriate engineer with specific fix instructions. Which files need changes and what exactly must change?`,
    { label: 'architect-sec-reroute', phase: 'Review', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-architect' }
  )
  await agent(
    `Fix security findings per Architect routing:\n${secRoute}\n\nOriginal security findings:\n${securityResult.findings}`,
    { label: 'security-fixes', phase: 'Review', agentType: 'senpai-team-v1-backend' }
  )
  const secRecheck = await agent(
    `Re-audit all security findings from the previous BLOCK. Verify each one is fully resolved. PASS or BLOCK.`,
    { schema: SECURITY_SCHEMA, label: 'security-recheck', phase: 'Review', model: 'claude-opus-4-8', agentType: 'senpai-team-v1-security' }
  )
  if (secRecheck && secRecheck.verdict === 'BLOCK') {
    log('DELIVERY BLOCKED — security re-check still failing.')
    return { blocked: 'Security', findings: secRecheck.findings }
  }
  log('Security re-check passed.')
}

// ── Phase 6: DevOps + Rollback (parallel) ────────────────────────────────────

phase('DevOps')

const devopsThunks = [
  () => agent(
    `Verify the sprint is deployable: migrations present and ordered, new env vars named and documented, app boots cleanly, zero-downtime status. Flag any deployment blockers. Do NOT run any git mutations.`,
    { label: 'devops', phase: 'DevOps', agentType: 'senpai-team-v1-devops' }
  )
]

if (plan.hasMigrations) {
  devopsThunks.push(() => agent(
    `Produce a concrete rollback guide — exact commands, not prose. Cover: git rollback (exact command), DB rollback (down script or manual SQL; mark irreversible if so), env var cleanup, verification steps. Must be scannable in 30 seconds under pressure.`,
    { label: 'rollback', phase: 'DevOps', agentType: 'senpai-team-v1-rollback' }
  ))
}

await parallel(devopsThunks)

// ── Phase 7: Docs & PR text (NO git mutations) ───────────────────────────────

phase('Docs & PR')

const digestCtx = [
  `Feature tasks passed: ${passedTaskNames.join(', ') || 'none'}`,
  `Patch tasks run: ${plan.patchTasks.map(t => t.name).join(', ') || 'none'}`,
  `Security: ${securityResult ? securityResult.verdict : 'n/a'}${securityResult && securityResult.lowFindings ? ' (low findings: ' + securityResult.lowFindings + ')' : ''}`,
  `Migrations: ${plan.hasMigrations}`,
  `QA rounds used: ${qaRound}`
].join('\n')

const docs = await agent(
  `Sprint is complete. Produce:\n1. Update any docs affected (API docs, README, env var references, setup guides).\n2. Sprint digest: what was built (one line per task), what affects deployments, open risks from UX/perf/security low findings.\n3. A PR description with ## Summary, ## Test plan, ## Risks.\n\nSprint context:\n${digestCtx}\n\nIMPORTANT: Do NOT run any git commands — no commit, no branch, no push, no PR creation. The orchestrator will handle committing/PR-opening only when the user explicitly asks. Return the digest and the PR description as text so the orchestrator can present them.`,
  { label: 'docs', phase: 'Docs & PR', agentType: 'senpai-team-v1-docs' }
)

log('Sprint complete (no git mutations performed — commit/PR is left to the user).')

return {
  mode: 'implement',
  featureTasksPassed:  passedTaskNames,
  patchTasksPassed:    patchQAResults.filter(r => r.verdict === 'PASS').map(r => r.taskName),
  patchTasksSkipped:   patchQAResults.filter(r => r.verdict === 'FAIL').map(r => r.taskName),
  securityVerdict:     securityResult ? securityResult.verdict : 'n/a',
  hasMigrations:       plan.hasMigrations,
  qaRoundsUsed:        qaRound,
  docsAndPr:           docs || ''
}
```

---

## 2. `gen_build.py`

```python
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
```
