# advisor-executor

A [Claude Code](https://claude.com/claude-code) skill for building **cost-optimized multi-model Claude agents**.

Two API features let you keep frontier-model judgment while billing most tokens at a cheaper model's rate. This skill teaches Claude to reach for the right one, wire it up correctly, and avoid the parts that bite.

| | **Advisor** | **Orchestrator** |
|---|---|---|
| Shape | A cheap executor calls *up* to a strong advisor | A strong coordinator delegates *down* to cheap workers |
| Feature | [`advisor_20260301`](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) tool on `/v1/messages` | [Managed Agents](https://platform.claude.com/docs/en/managed-agents/multi-agent) `multiagent: {type: "coordinator"}` |
| Strong model does | One-shot planning / course-correction | Planning, delegation, synthesis |
| Cheap model does | All tool calls and the final output | All token-heavy reading and doing |
| Anthropic's reported result | **SWE-bench Pro:** Sonnet 5 + Fable 5 advisor ≈ 92% of Fable-5-solo score at ≈ 63% of the price | **BrowseComp:** Fable 5 coordinator + Sonnet 5 workers ≈ 96% of Fable-5-solo performance at ≈ 46% of the price |

Those numbers are Anthropic's, on their benchmarks. Evaluate on your own workload.

**No API key?** The two features above are API-billed, but the *patterns* work on a Claude Pro/Max subscription too — there the scarce resource is your usage quota, and the same judgment/volume split stretches it. The skill covers the Claude Code equivalents: `opusplan`, an advisor subagent on a stronger model, Haiku scout subagents for fan-out reading, and the Agent SDK on a `claude setup-token`. Drop-in agent definitions included in `examples/claude-code/agents/`.

## Install

```bash
git clone https://github.com/lorsabyan/claude-advisor-executor-skill.git
cp -r claude-advisor-executor-skill/advisor-executor ~/.claude/skills/
```

Or drop `advisor-executor/` into a project's `.claude/skills/` to scope it to that repo.

## Use

The skill triggers on its own when you're working on an Anthropic API agent and the conversation turns to cost, model routing, escalation, or planner/worker designs. Or invoke it directly:

> Wire a Fable 5 advisor into this Sonnet agent loop and cap what it costs me.

> Our research agent burns $4 a question. Can we split it across models?

> Review this agent — are we paying frontier rates for mechanical work?

## What's inside

```
advisor-executor/
├── SKILL.md                        # which pattern, the shape of each, the 5 things that bite
├── references/
│   ├── advisor-tool.md             # full API surface: params, result variants, error codes,
│   │                               #   pause_turn, streaming, usage.iterations billing, caching,
│   │                               #   the model-compatibility matrix
│   ├── orchestrator.md             # Managed Agents: coordinator/worker setup, threads, events,
│   │                               #   MCP + vault scoping, the hard limits
│   ├── system-prompts.md           # verbatim prompt blocks, each with its measured effect AND
│   │                               #   its caveat (several help one executor and hurt another)
│   └── subscription-mode.md        # the same patterns on a Pro/Max subscription: opusplan,
│                                   #   advisor/scout subagents, Agent SDK on a setup-token
├── examples/
│   ├── advisor_loop.py             # complete loop: dispatch, pause_turn resumption, call cap,
│   ├── advisor_loop.ts             #   nudge, per-model cost accounting
│   ├── orchestrator.py             # coordinator + worker fan-out with per-thread streaming
│   └── claude-code/agents/         # drop-in advisor.md (opus) + scout.md (haiku) for .claude/agents/
└── scripts/
    └── validate_pair.py            # check an executor/advisor pair before the API 400s you
```

```bash
$ ./advisor-executor/scripts/validate_pair.py claude-sonnet-5 claude-haiku-4-5
✗ claude-haiku-4-5 cannot advise claude-sonnet-5 — the API will return a 400.
  Valid advisors for claude-sonnet-5: claude-fable-5, claude-mythos-5, claude-opus-4-8, claude-opus-4-7
  The advisor must be at least as capable as the executor.
```

## Why a skill rather than a doc link

The docs are complete but long, and the load-bearing details are scattered through them. The ones this skill front-loads:

- **`max_tokens` on the tool definition is the whole cost story.** The top-level `max_tokens` does *not* bound the advisor. Uncapped advisors emit ~4,200–5,900 tokens on hard tasks; `2048` cuts that ~7× with near-zero truncation and no measurable quality loss.
- **A Fable or Mythos advisor returns `advisor_redacted_result`** — an encrypted blob your client cannot read. Round-trip it verbatim anyway. Use `claude-opus-4-8` if you need to *see* the advice.
- **Dropping the advisor tool while `advisor_tool_result` blocks remain in the history is a `400`.** To stop advising mid-conversation you must remove the tool *and* strip the blocks.
- **`stop_reason: "pause_turn"`** means a dangling advisor call. Re-send unchanged; it can pause again.
- **Top-level `usage` excludes advisor tokens** — by design, since they bill at a different rate. Real cost accounting has to walk `usage.iterations[]`.
- **The prompt blocks are not universally good.** The nudge is worth ~+7 points on Haiku, does nothing on Sonnet, and *lowers* Opus pass rates. The Haiku coding block gains ~7.5 points on coding and loses ~4 on browse comprehension.
- **Executor/advisor pairs are validated server-side**, and the matrix is not symmetric.

## Sources

- [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) — Claude Docs
- [Multi-agent sessions](https://platform.claude.com/docs/en/managed-agents/multi-agent) — Claude Docs
- [Plan big, execute small](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb) — Claude Cookbooks

Not affiliated with Anthropic. Benchmark figures and prompt blocks are quoted from the public docs and cookbook linked above.

## License

MIT
