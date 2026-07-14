# Subscription mode — the same patterns without an API key

The `advisor_20260301` tool and Managed Agents are **API-billed features**. They run on `/v1/messages` and `/v1/sessions` with an API key, and there is no way to invoke them on a Claude Pro/Max subscription.

The **patterns**, however, carry over directly. On a subscription your scarce resource is the usage quota (5-hour windows, weekly caps) instead of dollars, and Opus/Fable-tier tokens draw it down much faster than Sonnet — roughly in proportion to the same per-token price gap that makes the API patterns pay. Splitting judgment from volume stretches a quota the same way it cuts a bill. Claude Code has native machinery for both splits.

## Advisor pattern in Claude Code

Three tiers, cheapest to set up first.

### 1. `opusplan` — the built-in

```
/model opusplan
```

Plan mode runs on Opus, execution runs on Sonnet. This *is* the advisor pattern — the strong model produces the plan, the cheap model does the substantive work — with zero configuration. If your advisor need is "one good plan up front," stop here.

What it doesn't give you: mid-task escalation. Once execution starts you're on Sonnet until you re-enter plan mode.

### 2. An advisor subagent — mid-task escalation

Run the session on Sonnet (`/model sonnet`) and define a stronger-model subagent the session escalates to. Drop `examples/claude-code/agents/advisor.md` into `.claude/agents/` (project) or `~/.claude/agents/` (global):

```markdown
---
name: advisor
description: Strategic advisor on a stronger model. Consult before committing to an approach on nontrivial tasks, when stuck, or before declaring a hard task done.
tools: Read, Grep, Glob, Bash
model: opus
---
You are a senior advisor consulted mid-task by a cheaper executor agent...
```

`model:` accepts the aliases `haiku` / `sonnet` / `opus` (or a full model ID such as `claude-fable-5`, if your plan includes it — check with `/model`).

**The key difference from the API advisor tool:** the API forwards the executor's *entire transcript* to the advisor automatically. A Claude Code subagent starts with a **fresh context** — it sees only the prompt the session writes when spawning it. So the escalation prompt must carry the state: the task, what's been tried, the key evidence, and the specific decision you want guidance on. In exchange you get something the API advisor lacks: the subagent has **tools**, so it can read the working tree, run `git diff`, and ground its advice in the actual repo state rather than the executor's summary of it.

| | API advisor tool | Claude Code advisor subagent |
|---|---|---|
| Sees full conversation | ✅ automatic | ❌ only what the prompt carries |
| Can inspect the repo itself | ❌ runs without tools | ✅ Read/Grep/Bash |
| Mid-turn (no round trip) | ✅ | ❌ a subagent spawn |
| Output cap | `max_tokens` on the tool | prompt instruction only |

To make the executor actually consult it, mirror the timing guidance from `system-prompts.md` in your project's `CLAUDE.md` — same logic, adapted to agent-spawning:

```markdown
## Advisor
For nontrivial tasks, consult the `advisor` agent (stronger model) BEFORE
substantive work — before writing, before committing to an interpretation.
Orientation (finding files, reading) first is fine. Also consult when stuck
(recurring errors, approach not converging) and before declaring a hard task
done. Pass it: the task, what you've tried, key evidence, and the specific
decision. Give its advice serious weight; if it contradicts evidence you've
gathered, say so in a follow-up consult rather than silently overriding it.
```

The same caveats from the API world apply: this raises consult frequency, which on trivial tasks is pure overhead. Don't add restraint language *and* pro-consult language — they conflict.

### 3. Manual escalation — zero setup

`/model sonnet` for the session; when you hit a genuinely hard decision, `/model opus` (or fable), ask the one question, switch back. Crude, but it's the full pattern under manual control, and it's often how you discover *where* your workload actually needs the strong model before automating it with option 2.

## Orchestrator pattern in Claude Code

Invert it: run the **session** on the strong model and push token-heavy work down to cheap subagents.

1. `/model opus` (or fable) for the main session — it plans, delegates, synthesizes.
2. Define workers with cheap `model:` overrides — see `examples/claude-code/agents/scout.md` (a Haiku read-only researcher). The built-in `Explore` agent type serves the same role where available.
3. Ask the session to fan out: independent subtasks dispatched **in parallel** (Claude Code runs agents concurrently when they're spawned in one message), each worker returning only its findings.

Context isolation — the property that makes Managed Agents cheap — is native here: each subagent has its own context window, and the thousand-line files a scout reads never enter the coordinator's context. Only the final report does. On a subscription this matters twice: the coordinator's (expensive) context stays small, *and* the volume tokens are billed against your quota at the cheap model's weight.

Guidance for the coordinator's `CLAUDE.md`, mirroring the cookbook's coordinator prompt:

```markdown
## Delegation
For research/audit tasks that fan out (many files, many sources): break the
task into focused sub-questions and dispatch each to a `scout` agent in
parallel. Do not read large files or long search results yourself — that is
scout work. You synthesize their reports.
```

## Agent SDK on a subscription

The Agent SDK can authenticate with a subscription instead of an API key: run `claude setup-token` once and export the result as `CLAUDE_CODE_OAUTH_TOKEN`. Per-agent model overrides then give you both patterns programmatically:

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const msg of query({
  prompt: "Audit the auth module for injection risks.",
  options: {
    model: "claude-sonnet-5",          // executor / session model
    agents: {
      advisor: {                        // escalate UP
        description: "Strategic advisor on a stronger model. Consult before committing to an approach, when stuck, or before declaring done.",
        model: "opus",
        tools: ["Read", "Grep", "Glob"],
        prompt: "You are a senior advisor consulted by a cheaper executor. Ground your advice in the repo, be specific, stay under 300 words.",
      },
      scout: {                          // delegate DOWN
        description: "Fast researcher for focused read-only sub-questions.",
        model: "haiku",
        tools: ["Read", "Grep", "Glob"],
        prompt: "Research exactly the sub-question you are given. Report findings with file:line evidence. Do not edit anything.",
      },
    },
  },
})) { /* ... */ }
```

Same trade as the CLI: no automatic transcript forwarding to the advisor, but the advisor gets tools.

## Day-to-day prompting

With the agents installed (`cp examples/claude-code/agents/{advisor,scout}.md ~/.claude/agents/`), the pattern is chosen per task by how you phrase the ask:

**Executor mode** (`/model sonnet`) — name the consult points explicitly:

> Fix the flaky retry logic in the sync module. Consult the advisor agent before committing to an approach, and again before you call it done.

The two anchors — *before the approach, before declaring done* — are the timings the API-side measurements identified as highest-value. Mid-task, "get a second opinion from the advisor on this" works whenever the session is spinning.

**Coordinator mode** (`/model opus` or fable) — phrase the task as fan-out and fence the strong model off from the reading:

> Map every place the backend touches file uploads — controllers, services, validation. Dispatch scout agents in parallel, one focused question each; don't read the large files yourself, synthesize their reports.

Without the "don't read the files yourself" clause, the strong model does scout work at coordinator prices.

**Plan mode** (`/model opusplan`) — nothing to phrase; prompt normally.

**Rollout advice:** run a week with explicit per-prompt consults before baking the CLAUDE.md block (above) into a repo — and put it in the repos where it earned its keep, project-level, not your global CLAUDE.md. Every measured regression in the API-side data came from forcing consults onto tasks that didn't need them; the subscription version has the same failure mode, spending quota instead of dollars.

**Upgrading the advisor:** if your plan includes Fable 5, change `model: opus` to `model: claude-fable-5` in the advisor agent file. Same workflow, stronger advisor, Sonnet still does all the volume — the tweet's SWE-bench configuration, running on a subscription.

## Choosing, on a subscription

- Quota pressure comes from **long sessions on Opus/Fable** → run the session on Sonnet, add the advisor subagent (or just use `opusplan`).
- Quota pressure comes from **bulk reading** (research, audits, large-repo sweeps) → keep the strong session, fan out to Haiku/Sonnet scouts.
- Both → strong session + cheap scouts, and reserve the session's own tokens for synthesis. This is the cookbook's "plan big, execute small" running entirely inside Claude Code.

And the same disqualifier as the API: if the task is one short question, none of this helps — just ask the model that's good enough to answer it.
