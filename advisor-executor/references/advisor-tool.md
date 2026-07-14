# Advisor tool — complete reference

Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool

Beta header: `advisor-tool-2026-03-01`. Available on the Claude API and Claude Platform on AWS. **Not** on Bedrock, Google Cloud, or Microsoft Foundry. Eligible for Zero Data Retention.

## Mechanics

1. Executor emits a `server_tool_use` block, `name: "advisor"`, **empty `input`**. The executor signals *timing* only; the server supplies context.
2. Anthropic runs a separate server-side inference on the advisor model. The advisor gets its own Anthropic-supplied system prompt plus the executor's **full transcript** as quoted context — your system prompt, tool definitions, prior turns, tool results, and the text the executor has produced so far this turn.
3. The advice returns as an `advisor_tool_result` block.
4. The executor keeps generating, informed by it.

All inside one `/v1/messages` request. The advisor runs **without tools** and **without context management**; its thinking blocks are dropped before the result returns.

## Tool parameters

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `type` | string | required | Must be `"advisor_20260301"` |
| `name` | string | required | Must be `"advisor"` |
| `model` | string | required | Advisor model ID. Billed at this model's rates. |
| `max_uses` | int | unlimited | Per-**request** cap. Further calls return `max_uses_exceeded` and the executor continues without advice. Not a per-conversation cap. |
| `max_tokens` | int | advisor's own cap | Caps advisor output (thinking + text) per call. Min 1024. |
| `caching` | object\|null | `null` | `{"type": "ephemeral", "ttl": "5m"\|"1h"}`. An on/off switch, **not** a breakpoint marker — the server picks cache boundaries. |

Also accepts the generic tool properties: `cache_control`, `allowed_callers`, `defer_loading`, `strict`.

## Response structure

```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Let me consult the advisor on this."},
    {"type": "server_tool_use", "id": "srvtoolu_abc123", "name": "advisor", "input": {}},
    {"type": "advisor_tool_result",
     "tool_use_id": "srvtoolu_abc123",
     "content": {"type": "advisor_result",
                 "text": "Use a channel-based coordination pattern. The tricky part is draining in-flight work during shutdown..."}},
    {"type": "text", "text": "Here's the implementation..."}
  ]
}
```

### Result variants

`advisor_tool_result.content` is a discriminated union.

| Variant | Fields | When |
|---|---|---|
| `advisor_result` | `text`, `stop_reason` | Advisor returns plaintext — Opus 4.6/4.7/4.8, Sonnet 4.6 |
| `advisor_redacted_result` | `encrypted_content`, `stop_reason` | **Fable 5 and Mythos 5** — opaque blob, your client cannot read it |

`stop_reason` is present only when you set `max_tokens` on the tool definition; it holds the sub-call's stop reason (`end_turn`, or `max_tokens` when capped).

Round-trip both variants **verbatim** on later turns. The server decrypts `encrypted_content` into the executor's prompt, so the executor always sees plaintext regardless. If you switch advisor models mid-conversation, branch on `content.type`.

### Error results

The request does **not** fail; the executor sees the error and continues.

```json
{"type": "advisor_tool_result",
 "tool_use_id": "srvtoolu_abc123",
 "content": {"type": "advisor_tool_result_error", "error_code": "overloaded"}}
```

| `error_code` | Meaning |
|---|---|
| `max_uses_exceeded` | Hit the `max_uses` cap for this request |
| `too_many_requests` | Advisor sub-inference rate-limited |
| `overloaded` | Advisor sub-inference at capacity |
| `prompt_too_long` | Transcript exceeded the advisor's context window |
| `execution_time_exceeded` | Advisor sub-inference timed out |
| `unavailable` | Any other advisor failure |

Advisor rate limits draw from the **same per-model bucket** as direct calls to that model. A rate limit on the *advisor* shows up as `too_many_requests` inside the tool result; a rate limit on the *executor* fails the whole request with HTTP 429.

## Multi-turn

Append the full assistant `content` — advisor blocks included — to `messages` each turn.

**`400 invalid_request_error` if** the history contains `advisor_tool_result` blocks and you omit the advisor tool from `tools`. There is no built-in conversation-level cap. To enforce one: count calls client-side, and when you hit your ceiling remove the tool **and** strip every `advisor_tool_result` block from the history.

### Resuming a paused turn

A response can end with `stop_reason: "pause_turn"` — a `server_tool_use` block with no matching result. To resume: append that assistant message with its content **unchanged** (keep the `server_tool_use` block) and re-send with the same advisor tool and beta header. No user message, no `tool_result` block needed. The API runs the pending advisor call and continues the turn. It can pause again; repeat. Omitting the advisor tool on the resume request is a `400`.

If the executor also called one of *your* tools in that turn, the response ends with `stop_reason: "tool_use"` instead. Send your `tool_result` blocks as usual; the pending advisor call runs at the start of that next request.

## Streaming

The advisor sub-inference **does not stream**. The executor's stream pauses when the `server_tool_use` block closes (`content_block_stop`). During the pause the stream is quiet except SSE `ping` keepalives roughly every 30s (short calls may show none). The `advisor_tool_result` then arrives fully formed in a single `content_block_start` — no deltas. Executor output resumes, and a `message_delta` follows with the updated `usage.iterations`.

## Usage and billing

```json
{"usage": {
  "input_tokens": 412, "output_tokens": 531,
  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
  "iterations": [
    {"type": "message", "input_tokens": 412, "output_tokens": 89},
    {"type": "advisor_message", "model": "claude-fable-5",
     "input_tokens": 823, "output_tokens": 1612},
    {"type": "message", "input_tokens": 1348, "cache_read_input_tokens": 412, "output_tokens": 442}
  ]}}
```

- **Top-level `usage` is executor-only.** Advisor tokens are deliberately not rolled in — they bill at a different rate.
- `iterations[].type == "advisor_message"` bills at the advisor's rates; `"message"` bills at the executor's.
- Top-level `output_tokens` = sum of executor iterations. Top-level `input_tokens` / `cache_read_input_tokens` = **first executor iteration only** (later inputs include prior outputs and would double-count).
- Use `usage.iterations` for any real cost accounting.
- Typical advisor output on light workloads: 400–700 text tokens, 1,400–1,800 including thinking. On hard reasoning tasks, far more — see the cap table below.
- Top-level `max_tokens` bounds executor output only. Advisor tokens also don't draw from a task budget applied to the executor.
- **Priority Tier applies per model.** A commitment on the executor does not extend to the advisor.

## Caching

Two independent layers.

**Executor-side:** `advisor_tool_result` is a normal cacheable block. A `cache_control` breakpoint placed after it on a later turn hits. Identical for both result variants (the executor's prompt always holds plaintext).

**Advisor-side:** set `caching` on the tool definition. The advisor's Nth prompt is the (N-1)th plus one appended segment, so the prefix is stable; each call writes a cache entry and the next reads it and pays only the delta. You'll see non-zero `cache_read_input_tokens` on the 2nd+ `advisor_message` iteration.

- **Break-even is ~3 advisor calls per conversation.** At ≤2 the write costs more than the reads save. Enable for long agent loops; leave off for short tasks.
- Set it once and leave it. Toggling mid-conversation causes misses.
- ⚠️ `clear_thinking` with `keep` other than `"all"` shifts the advisor's quoted transcript each turn → advisor-side cache misses. Cost degradation only; advice quality is unaffected. When extended thinking is on without explicit `clear_thinking` config, the API defaults to `keep: {type: "thinking_turns", value: 1}` on earlier Opus/Sonnet and **all Haiku** models. Set `keep: "all"` for cache stability.

## Combining with other tools

Advisor composes with server-side and client-side tools in the same `tools` array — the executor can search the web, consult the advisor, and call your custom tools in one turn, and the advice can steer which tool it reaches for next.

| Feature | Interaction |
|---|---|
| Batch processing | Supported. `usage.iterations` reported per item. |
| Token counting | Returns executor first-iteration input only. For an advisor estimate, call `count_tokens` with `model` set to the advisor and the same messages. |
| Context editing | `clear_tool_uses` is **not fully compatible** with advisor blocks. For `clear_thinking`, see the caching warning. |
| Extended thinking | Cannot be combined with `tool_choice` forcing the advisor — `400`. |

## Capping advisor output

Measured on a hard reasoning benchmark (n=40 per config):

| `max_tokens` | Mean advisor output | Calls truncated |
|---|---|---|
| unset | ~4,200–5,900 | n/a |
| **2048** | ~630–840 | ~0% |
| 1024 | ~370–480 | ~10% |

Recommended start: **2048** (~7× reduction, no detectable quality loss; accuracy differences across configs were within noise at that sample size). Minimum 1024. Above the advisor's own output cap → `400`. Per-call, not shared across calls in a request.

Not a blind truncation — the server passes the advisor its remaining budget, so it shapes the response to fit. On hitting the cap, the result carries `stop_reason: "max_tokens"` and the API appends `[Advisor output truncated at max_tokens=2048.]` to the text so the executor sees it. Both signals appear only when you set `max_tokens` on the tool.

## Pairing with effort

For coding, a **Sonnet executor at medium effort + Opus advisor** reaches intelligence comparable to Sonnet at default effort, at lower cost. For maximum intelligence keep the executor at default effort.

## Model compatibility

The advisor must be **Sonnet 4.6 or more capable**, and **at least as capable as the executor**. Equal-capability models may advise each other. Invalid pairs → `400` naming the combination.

| Executor | Valid advisors |
|---|---|
| `claude-haiku-4-5` | fable-5, mythos-5, opus-4-8, opus-4-7, opus-4-6, sonnet-4-6 |
| `claude-sonnet-4-6` | fable-5, mythos-5, opus-4-8, opus-4-7, opus-4-6, sonnet-4-6 |
| `claude-sonnet-5` | fable-5, mythos-5, opus-4-8, opus-4-7 |
| `claude-opus-4-6` | fable-5, mythos-5, opus-4-8, opus-4-7, opus-4-6 |
| `claude-opus-4-7` | fable-5, mythos-5, opus-4-8, opus-4-7 |
| `claude-opus-4-8` | fable-5, mythos-5, opus-4-8, opus-4-7 |
| `claude-fable-5` | fable-5 |
| `claude-mythos-5` | mythos-5 |

`scripts/validate_pair.py` encodes this table.

## Fit

Good: long-horizon agentic workloads — coding agents, computer use, multi-step research — where most turns are mechanical but the plan matters.

- Currently on Sonnet for complex tasks → add a higher-tier advisor. Opus keeps total cost similar or lower; Fable 5 maximizes the quality lift.
- Currently on Haiku and want more intelligence → add an Opus or Fable advisor. Costs more than Haiku alone, less than moving the executor up a tier.

Poor: single-turn Q&A (nothing to plan); pass-through model pickers where users already choose their own cost/quality tradeoff; workloads where every turn genuinely needs the advisor's full capability.
