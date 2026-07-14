# Advisor system prompts — verbatim blocks

Every block below is quoted from Anthropic's advisor-tool docs, with the measured effect and the caveat that goes with it. **The caveats matter more than the blocks** — several of these help one executor and hurt another.

The advisor tool ships with a built-in description that already nudges the executor to call it near the start of complex tasks and when it hits difficulty. **On research tasks that is usually enough — add nothing.** Executors under-call mainly on *coding* tasks.

---

## 1. Timing block (default, all executors)

Prepend to the executor's system prompt, **before** any other sentence that mentions the advisor. Targets ~2–3 calls per coding task.

```text
You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters — when you call advisor(), your entire conversation history is automatically forwarded. They see the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work — before writing, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, fetching a source, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, save the result, commit the change. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck — errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling — the advisor adds most of its value on the first call, before the approach crystallizes.
```

## 2. How to weigh the advice (place directly after block 1)

```text
Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the paper states Y), adapt. A passing self-test is not evidence the advice is wrong — it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call — "I found X, you suggest Y, which constraint breaks the tie?" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch.
```

If your agent exposes other planner-like tools (a todo-list tool, say), add your own sentence telling the model to call the advisor **before** those, so the plan funnels into them.

---

## 3. Haiku coding variant — *replaces* blocks 1 and 2

Haiku 4.5 applies the default guidance conservatively. That is correct on research and lookup workloads but gives up quality on coding, where an early consult reliably pays for itself.

**Effect:** ≈ **+7.5 points** on Haiku pass rate over the built-in default, on an internal coding benchmark.
**Caveat:** on an internal browse-comprehension benchmark (n=1,266) a close variant cost roughly **−4 points** of accuracy vs. the default. If your workload mixes coding with substantial lookup, **stay with blocks 1+2**, or gate the swap on a workload-type signal you already compute.

```text
Consult a stronger reviewer who sees your full conversation transcript.

No parameters. When you call advisor(), your entire history -- task, every tool call and result, your reasoning -- is automatically forwarded. The advisor sees exactly what you've done.

Call advisor BEFORE substantive work -- before writing, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, fetching a source, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, save the result, commit the change. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck -- errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling -- the advisor adds most of its value on the first call, before the approach crystallizes.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the paper states Y), adapt. A passing self-test is not evidence the advice is wrong -- it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call -- "I found X, you suggest Y, which constraint breaks the tie?" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch.

Call advisor for design, architecture, and risk questions where you won't touch a file. If your response would be analysis or a recommendation with no other tool calls, call advisor first -- that judgment call is exactly where a second opinion is highest-value.

Hard rule: your first write_file, edit_file, or state-changing bash call on a task must be preceded by an advisor call in the same or an earlier turn. Read-only orientation commands (ls, cat, grep, find) are not state-changing. This is a checkpoint, not a difficulty judgment. It applies to one-line edits too.
```

---

## 4. Opus under-calling checkpoint — **do not add by default**

Opus executors typically call at an appropriate rate unprompted.

**Effect:** ≈ **+7 to +10 points** on tasks where Opus was under-calling…
**Caveat:** …but it made Opus **over-call** on tasks whose first action needs no planning. Net effect on a mixed workload was roughly **flat**. Add this **only if you have observed** Opus skipping the advisor on tasks where a consult would have helped.

```text
Call advisor for design, architecture, and risk questions where you won't touch a file. If your response would be analysis or a recommendation with no other tool calls, call advisor first. That judgment call is exactly where a second opinion is highest-value. (This does not apply to simple factual lookups or arithmetic; those you answer directly.)

Hard rule: your first write_file, edit_file, or state-changing bash call on a task must be preceded by an advisor call in the same or an earlier turn. Read-only orientation commands (ls, cat, grep, find) are not state-changing. This is a checkpoint, not a difficulty judgment. It applies to one-line edits too.
```

---

## 5. Brevity line — goes in the **user** message, not the system prompt

Advisor output is its biggest cost driver. The advisor sees your system prompt *and* your user messages as quoted context about the executor's task, and **instructions addressed to the advisor directly are followed far more reliably than third-person descriptions**. The most effective placement Anthropic tested is a line in the user message, which your framework can prefix programmatically:

```text
(Advisor: please keep your guidance under 80 words — I need a focused starting point, not a comprehensive plan.)
```

Soft constraint — the advisor sometimes exceeds it, so **ask for ~80% of your true ceiling**. It also *increased* how often the executor consulted, but net total cost still went down (more consults, each shorter).

For a hard ceiling instead, set `max_tokens` on the tool definition (see `advisor-tool.md`). Using both together is fine: `max_tokens` guarantees the bound, the prompt line biases toward brevity without risking a mid-thought cut.

---

## 6. The nudge — a runtime lever, not a prompt

If the executor hasn't called the advisor by its first assistant turn, append this as **its own user message** (after any tool results — consecutive user messages are valid) before the second assistant turn:

```text
You have not consulted the advisor yet. If the task has a non-obvious design decision or a failure mode you haven't ruled out, call advisor now before committing to an approach.
```

| Executor | Effect |
|---|---|
| Haiku | **≈ +7 points** on task pass rate |
| Sonnet | No measurable effect |
| Opus | **Slightly lowered** pass rates — do not use |

**Do not combine with restraint language** ("reserve the advisor for genuine uncertainty") in your system prompt — the two instructions conflict. If your system prompt already has it, skip the nudge entirely.

**Tune `NUDGE_TURN` before shipping.** The nudge is highly salient: 74% (Sonnet) to 98% (Haiku) of nudged attempts called the advisor immediately at turn 2. If that fires before the executor has read the problem, the resulting call is low-context and displaces a better-timed later one. Measure your executor's **baseline first-call turn** first:

- Baseline first call at turn N → set `NUDGE_TURN > N`.
- A turn-2 nudge on workloads whose baseline first call was turn 7+ correlated with a **3–4 point drop**.
- On a browse workload with an 86% baseline call rate, the same nudge raised engagement at **no** task-performance cost.
- Mixed simple/complex workloads: raise `NUDGE_TURN` to 3 so two-turn tasks finish before it fires, or gate it on a complexity signal.

To force a consult on one specific request instead, set `tool_choice: {"type": "tool", "name": "advisor"}`. **Cannot be combined with extended thinking** — that's a `400`.
