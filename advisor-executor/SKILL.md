---
name: advisor-executor
description: Cut the cost of Claude agents by splitting the work across models — a cheap executor with a strong advisor tool (advisor_20260301), or a strong orchestrator delegating to cheap workers (Managed Agents multiagent). Use when building or reviewing an Anthropic API agent and the ask involves cost, price, spend, budget, token bill, "too expensive", model choice/routing/escalation, using Fable/Opus to steer Sonnet/Haiku, planner-worker or coordinator-subagent designs, or getting frontier quality without frontier cost.
license: MIT
---

# Advisor & orchestrator patterns

Two ways to get near-frontier agent quality while billing most tokens at a cheaper model's rate. Both are first-class API features, not prompt tricks.

| | **Advisor** | **Orchestrator** |
|---|---|---|
| Shape | Cheap executor calls up to a strong advisor | Strong coordinator delegates down to cheap workers |
| Feature | `advisor_20260301` tool on `/v1/messages` | Managed Agents `multiagent: {type: "coordinator"}` |
| Strong model does | One-shot planning / course-correction | Planning, delegation, synthesis |
| Cheap model does | All tool calls and the final output | All token-heavy reading and doing |
| Anthropic's reported result | SWE-bench Pro: Sonnet 5 + Fable 5 advisor ≈ 92% of Fable-5-solo score at ≈ 63% of the price | BrowseComp: Fable 5 coordinator + Sonnet 5 workers ≈ 96% of Fable-5-solo performance at ≈ 46% of the price; cookbook measured 2.5× cheaper and 3× faster than a solo frontier agent |

## Which one

Ask what the expensive part of the workload is.

**Expensive part is the thinking, cheap part is the doing → advisor.** The task is one coherent line of work (fix this bug, drive this browser, run this pipeline) where most turns are mechanical but a wrong plan is costly. One process, one context, one `/v1/messages` request. Reach for this first — it is a single field in `tools` and works with the plain Messages API.

**Expensive part is the reading, and it fans out → orchestrator.** The task splits into independent subtasks that each burn a lot of input tokens (research many sources, audit many files). Workers get isolated contexts, so the giant pages one worker reads never enter anyone else's context. Requires Managed Agents (sandbox, sessions, `agent_toolset_20260401`).

**Both.** Managed Agents sub-agents support escalating up and delegating down in the same roster. A coordinator can delegate to Sonnet workers that each carry a Fable advisor.

Neither pattern fits single-turn Q&A (nothing to plan), or workloads where every turn genuinely needs the frontier model.

## Advisor: the shape

The executor decides when to consult. The server hands the advisor the executor's full transcript, runs it as a separate server-side inference, and returns the advice inline. No extra round trip on your side.

```python
tools = [
    {
        "type": "advisor_20260301",   # required, exact
        "name": "advisor",            # required, exact
        "model": "claude-fable-5",    # billed at THIS model's rates
        "max_tokens": 2048,           # cap advisor output — see below
    },
    # ... your own tools
]

response = client.beta.messages.create(
    model="claude-sonnet-5",                 # executor
    max_tokens=4096,                         # executor output only
    betas=["advisor-tool-2026-03-01"],       # required beta header
    tools=tools,
    messages=messages,
)
```

Five things that bite people, in the order they usually bite:

1. **`max_tokens` on the tool definition is the single highest-leverage setting.** Advisor output is the advisor's biggest cost driver and the top-level `max_tokens` does *not* bound it. Uncapped advisors emit ~4,200–5,900 tokens on hard tasks. Start at **2048** — Anthropic measured ~7× less advisor output with near-zero truncation and no detectable quality loss. Minimum is 1024, but that truncates ~10% of calls.
2. **Round-trip the whole assistant content on later turns**, `advisor_tool_result` blocks included. With a Fable or Mythos advisor those blocks are `advisor_redacted_result` — opaque `encrypted_content` your client cannot read. Pass them back verbatim anyway; the server decrypts them into the executor's prompt. Want to *see* the advice? Use `claude-opus-4-8` as the advisor, which returns plaintext `advisor_result`.
3. **Never drop the tool while the history still holds advisor blocks.** Omitting the advisor from `tools` with `advisor_tool_result` blocks still in `messages` is a `400`. To stop advising mid-conversation you must remove the tool **and** strip those blocks.
4. **Handle `stop_reason: "pause_turn"`.** A dangling advisor call ends the response with a `server_tool_use` block and no result. Re-send the messages unchanged, same tools, same beta header; the advisor runs on resumption.
5. **Executor/advisor pairs are validated.** The advisor must be Sonnet 4.6 or stronger *and* at least as capable as the executor. An invalid pair is a `400`. Run `scripts/validate_pair.py <executor> <advisor>` to check before you ship.

Everything else — streaming, usage accounting, caching, error codes, resumption — is in **`references/advisor-tool.md`**. Read it before writing the agent loop; the `usage.iterations[]` billing shape in particular is not what you would guess (top-level `usage` counts executor tokens only).

## Advisor: making the executor call it well

The tool ships with a built-in description, and on research tasks that is usually enough. On **coding tasks executors under-call**, which is where the quality is lost. Two levers, in order:

- **System prompt.** Prepend the timing + how-to-weigh-advice blocks to the executor's system prompt. Verbatim, copy-pasteable, with the per-executor variants (Sonnet/Haiku/Opus behave differently, and the Haiku coding block is a *different* block that costs accuracy on lookup workloads) in **`references/system-prompts.md`**.
- **Nudge.** If the executor hasn't consulted by turn 2, append a plain user message telling it to. Worth ~7 points on Haiku; measurably *negative* on Opus; no effect on Sonnet. Do not combine it with restraint language in the system prompt — they conflict. Details and the `NUDGE_TURN` tuning caveat are in the reference.

Good timing is: one call early, after a few exploratory reads are in the transcript but before committing to an approach; and on hard tasks one more after the writes and test output land. Enable advisor-side `caching` only when you expect **3+ advisor calls** in a conversation — below that the cache write costs more than the reads save.

## Orchestrator: the shape

The coordinator holds no tools of its own. It delegates through its roster.

```python
worker = client.beta.agents.create(
    name="search-worker",
    model="claude-sonnet-5",
    tools=[{"type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [{"name": "web_search", "enabled": True},
                        {"name": "web_fetch", "enabled": True}]}],
    system="You are a search worker researching one focused sub-question ...",
)

coordinator = client.beta.agents.create(
    name="search-coordinator",
    model="claude-fable-5",
    tools=[{"type": "agent_toolset_20260401"}],
    multiagent={"type": "coordinator",
                "agents": [{"type": "agent", "id": worker.id}]},
    system="Break the question into focused sub-questions and delegate each "
           "to a worker. Your workers have web_search and web_fetch; you do not.",
)

session = client.beta.sessions.create(agent=coordinator.id,
                                      environment_id=environment.id)
```

Load-bearing constraints: delegation is **one level deep** (depth > 1 is ignored), max **20 agents** in a roster and **25 concurrent threads**, and the roster is **snapshotted at coordinator-create time** — referenced agents stay pinned to the version resolved then, so updating a worker does nothing until you update the coordinator. MCP servers are agent-scoped but vault credentials are session-scoped. Beta header is `managed-agents-2026-04-01`.

Full setup, thread lifecycle, event streaming, and cross-thread tool-permission routing: **`references/orchestrator.md`**.

## Files

- `references/advisor-tool.md` — complete advisor API surface: parameters, result variants, error codes, `pause_turn`, streaming, `usage.iterations[]` billing, caching, model compatibility matrix.
- `references/orchestrator.md` — Managed Agents multi-agent: coordinator/worker setup, threads, events, MCP + vault scoping, limits.
- `references/system-prompts.md` — verbatim system-prompt blocks for advisor timing, advice-weighting, the Haiku coding variant, the Opus checkpoint, and the advisor brevity line, each with its measured effect and caveat.
- `examples/advisor_loop.py` / `examples/advisor_loop.ts` — a complete agent loop: tool dispatch, `pause_turn` resumption, nudge, conversation-level call cap, per-model cost accounting from `usage.iterations`.
- `examples/orchestrator.py` — coordinator + worker fan-out with per-thread streaming.
- `scripts/validate_pair.py` — check an executor/advisor pair against the compatibility matrix before you get a `400`.

## Don't

- Don't put anything in `server_tool_use.input` — it is always empty and nothing you put there reaches the advisor. The server builds the advisor's view from the transcript.
- Don't force the advisor with `tool_choice` while extended thinking is on — that combination is a `400`.
- Don't assume top-level `usage` includes advisor tokens. It doesn't. Sum `usage.iterations[]` where `type == "advisor_message"` and bill those at the advisor's rate.
- Don't reach for either pattern to rescue a workload that is slow or wrong for reasons unrelated to model tier. Measure where the tokens actually go first.
- Don't quote the benchmark numbers above as guarantees. They are Anthropic's results on SWE-bench Pro / BrowseComp. Evaluate on your own workload.
