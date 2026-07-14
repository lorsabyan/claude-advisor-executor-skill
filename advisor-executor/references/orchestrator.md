# Orchestrator (Managed Agents multi-agent) — reference

Sources:
- https://platform.claude.com/docs/en/managed-agents/multi-agent
- https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb

Beta header: `managed-agents-2026-04-01` (memory store endpoints use `agent-memory-2026-07-22` instead). SDKs set it automatically.

## The idea

Most agent workloads contain two different jobs: a small amount of planning and judgment, and a large amount of mechanical reading and doing. Put the frontier model on the first and cheap models on the second.

All agents share one sandbox, filesystem, and vault credentials, but each runs in its own **session thread** — a context-isolated event stream with its own history. That isolation is the whole point: the giant web pages a worker reads never enter anyone else's context. Threads persist, so the coordinator can send a follow-up to an agent it called earlier and that agent still has everything from its previous turns.

Each agent has its own model, system prompt, tools, MCP servers, and skills. Tools, MCP servers, and context are **not** shared.

**Cookbook results (BrowseComp-style research):** split team $1.61 vs. solo frontier agent $4.00 — ~2.5× cheaper and ~3× faster wall-clock, with 84–98% of input tokens billed at the worker rate. Both read similar volumes; the team just read in parallel at cheaper rates.

## What to delegate

- **Parallelization** — independent subtasks (multiple sources, separate files), coordinator synthesizes.
- **Specialization** — domain-focused agents (a security agent, a docs agent) instead of one agent loaded with every capability.
- **Escalation** — consult a more capable agent/model for a subset of hard subtasks. (This is the advisor pattern, expressed as a sub-agent.)

Best for **coverage** tasks that need a lot of reading. Discovery tasks reward frontier intuition more, so they benefit less.

## Setup

The coordinator holds **no tools of its own** — it only delegates.

```python
worker = client.beta.agents.create(
    name="search-worker",
    model="claude-sonnet-5",
    tools=[{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": False},
        "configs": [
            {"name": "web_search", "enabled": True},
            {"name": "web_fetch", "enabled": True},
        ],
    }],
    system=(
        "You are a search worker researching one focused sub-question for "
        "a coordinator. Use web_search and web_fetch to find the answer. "
        "Be thorough: try multiple query phrasings, follow promising "
        "links, and cross-check facts across sources. Report back with "
        "the specific answer you found and the evidence (URLs, quotes) "
        "that supports it."
    ),
)

coordinator = client.beta.agents.create(
    name="search-coordinator",
    model="claude-fable-5",
    tools=[{"type": "agent_toolset_20260401"}],
    multiagent={
        "type": "coordinator",
        "agents": [{"type": "agent", "id": worker.id}],
    },
    system=(
        "You are coordinating a team of search workers to answer a hard "
        "web-research question. Your workers have web_search and "
        "web_fetch; you do not. Break the question into focused "
        "sub-questions and delegate each to a worker via create_agent."
    ),
)

session = client.beta.sessions.create(
    agent=coordinator.id,
    environment_id=environment.id,
)
```

### Roster entries

`multiagent.agents` accepts:

- `{"type": "agent", "id": agent.id}` — pinned to the **latest version at coordinator-create time**.
- `{"type": "agent", "id": agent.id, "version": agent.version}` — pinned to a specific version.
- `{"type": "self"}` — the coordinator can spawn copies of itself. Session-level agent config overrides apply to these copies (and to the coordinator), but **not** to entries referenced by ID.

⚠️ **The roster is snapshotted when the coordinator is created or updated.** Referenced agents stay pinned and do **not** pick up later edits to their definitions. To delegate to a newer worker version you must **update the coordinator**.

### Limits

| Limit | Value |
|---|---|
| Delegation depth | **1 level.** Depth > 1 is ignored. |
| Unique agents in a roster | 20 (but the coordinator may call multiple copies of each) |
| Concurrent threads | 25 |

## MCP servers and vaults

**MCP servers are agent-scoped** (each agent definition declares its own) while **vault credentials are session-scoped** (`vault_ids` at session creation apply to every thread). Two consequences:

- Include a vault credential for **every** MCP server used across **all** agents.
- To restrict an agent's access, declare only the servers it needs in its own definition.

```python
research_agent = client.beta.agents.create(
    name="researcher",
    model="claude-haiku-4-5",
    mcp_servers=[{"type": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp/"}],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "github"}],
)
coordinator = client.beta.agents.create(
    name="coordinator", model="claude-opus-4-8",
    tools=[{"type": "agent_toolset_20260401"}],
    multiagent={"type": "coordinator", "agents": [{"type": "agent", "id": research_agent.id}]},
)
session = client.beta.sessions.create(
    agent=coordinator.id, environment_id=environment.id, vault_ids=[vault.id],
)
```

Only the researcher declares the GitHub server, so the coordinator has no access to it; the session's `vault_ids` supply the credential to the researcher's thread.

💡 If an agent's MCP calls fail to authenticate after you declare the server, check that the credential's `mcp_server_url` matches the agent's `mcp_servers[].url` **exactly** — scheme and trailing slash included.

## Threads

The **session-level event stream** (`/v1/sessions/:id/events/stream`) is the **primary thread**: a condensed view of all activity. You see sub-agents start and finish and any blocking events, but not their full activity. Drill into a specific agent via its **session thread**. `parent_thread_id` is null for the primary thread.

Session `status` aggregates all threads — if any thread is `running`, the session is `running`.

```python
for thread in client.beta.sessions.threads.list(session.id):
    print(f"[{thread.agent.name}] {thread.status}")

# Stream one agent's activity
with client.beta.sessions.threads.events.stream(thread.id, session_id=session.id) as stream:
    for event in stream:
        match event.type:
            case "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="")
            case "session.thread_status_idle":
                break
```

**Interrupt:** send `user.interrupt` with `session_thread_id` (omit it to target the primary thread). Against a child thread blocked on `requires_action`, this marks each pending tool call denied and re-emits `session.thread_status_idle` with `stop_reason: end_turn` — the model is not sampled. Against an already-idle thread it's a no-op.

**Archive:** frees a slot against the 25-thread limit. Only succeeds if the thread is `idle` — interrupt first if it's running or blocked.

```python
client.beta.sessions.events.send(session.id,
    events=[{"type": "user.interrupt", "session_thread_id": thread.id}])
archived = client.beta.sessions.threads.archive(thread.id, session_id=session.id)
```

### Primary-thread events

| Type | Description |
|---|---|
| `session.thread_created` | Thread created. Has `session_thread_id`, `agent_name`. |
| `session.thread_status_running` | Thread started activity. |
| `session.thread_status_idle` | Agent awaiting input. Has `stop_reason`. |
| `session.thread_status_terminated` | Archived or terminal error. |
| `agent.thread_message_received` | An agent delivered its result to the coordinator. Has `from_session_thread_id`, `from_agent_name`, `content`. |
| `agent.thread_message_sent` | Coordinator sent a follow-up to an agent. Has `to_session_thread_id`, `to_agent_name`, `content`. |

## Tool permissions and custom tools across threads

If a sub-agent needs something from your client — permission for an `always_ask` tool, or a custom tool's result — the event is **cross-posted to the primary thread** with `session_thread_id` identifying where it came from:

```json
{"type": "session.thread_status_idle",
 "id": "sevt_01ABC...",
 "session_thread_id": "sth_01DEF...",
 "agent_name": "code-reviewer",
 "stop_reason": {"type": "requires_action", "event_ids": ["toolu_01XYZ..."]}}
```

Reply on the **session**, not the thread — post `user.tool_confirmation` (with `tool_use_id`) or `user.custom_tool_result` (with `custom_tool_use_id`) and the server routes it to the right thread automatically.

```python
for event_id in stop.event_ids:
    client.beta.sessions.events.send(session.id, events=[{
        "type": "user.tool_confirmation",
        "tool_use_id": event_id,
        "result": "allow",
    }])
```
