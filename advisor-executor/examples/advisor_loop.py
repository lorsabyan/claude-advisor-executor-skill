"""Complete advisor-tool agent loop: Sonnet 5 executor + Fable 5 advisor.

Covers the things a naive loop gets wrong:
  - pause_turn resumption (dangling advisor call)
  - round-tripping advisor_tool_result blocks verbatim
  - a conversation-level call cap (the API has none)
  - the turn-2 nudge, gated on executor family
  - cost accounting from usage.iterations (top-level usage is executor-only)

Run:  ANTHROPIC_API_KEY=... python advisor_loop.py
"""

import os

import anthropic

EXECUTOR = "claude-sonnet-5"
ADVISOR = "claude-fable-5"
BETA = "advisor-tool-2026-03-01"

MAX_TURNS = 10
MAX_ADVISOR_CALLS = 3  # conversation-level cap; the API only caps per-request

# The nudge helps Haiku (~+7pts), does nothing for Sonnet, and HURTS Opus.
# Tune NUDGE_TURN against your executor's baseline first-call turn before shipping.
NUDGE_TURN = 2
NUDGE_TEXT = (
    "You have not consulted the advisor yet. If the task has a non-obvious "
    "design decision or a failure mode you haven't ruled out, call advisor "
    "now before committing to an approach."
)
USE_NUDGE = EXECUTOR.startswith("claude-haiku")

# Soft brevity request. Addressed to the advisor directly — it reads user
# messages as quoted context, and follows second-person instructions far more
# reliably than third-person ones. Ask for ~80% of your true ceiling.
BREVITY = "(Advisor: please keep your guidance under 80 words — I need a focused starting point, not a comprehensive plan.)"

SYSTEM = """You are a coding agent.

You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters — when you call advisor(), your entire conversation history is automatically forwarded. They see the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work — before writing, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, fetching a source, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, save the result, commit the change.
- When stuck — errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim, adapt. A passing self-test is not evidence the advice is wrong — it's evidence your test doesn't check what the advice is checking."""


def advisor_tool():
    return {
        "type": "advisor_20260301",
        "name": "advisor",
        "model": ADVISOR,
        # Highest-leverage cost setting. Uncapped advisors emit ~4-6k tokens on
        # hard tasks; 2048 cuts that ~7x with near-zero truncation. Min is 1024.
        "max_tokens": 2048,
        # Advisor-side cache breaks even at ~3 calls/conversation. Below that the
        # write costs more than the reads save. Set once; toggling causes misses.
        "caching": {"type": "ephemeral", "ttl": "5m"} if MAX_ADVISOR_CALLS >= 3 else None,
    }


MY_TOOLS = [
    {
        "name": "run_bash",
        "description": "Run a bash command in the repo.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def run_my_tools(content):
    """Replace with real dispatch. One tool_result per tool_use block."""
    return [
        {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"(stub output for: {block.input})",
        }
        for block in content
        if block.type == "tool_use"
    ]


def count_advisor_calls(content):
    return sum(
        1 for b in content if b.type == "server_tool_use" and b.name == "advisor"
    )


def strip_advisor_blocks(messages):
    """Required before dropping the advisor tool: leaving advisor_tool_result
    blocks in the history with no advisor tool in `tools` is a 400."""
    cleaned = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            content = [
                b
                for b in content
                if getattr(b, "type", None)
                not in ("server_tool_use", "advisor_tool_result")
            ]
            if not content:
                continue
        cleaned.append({**msg, "content": content})
    return cleaned


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    task = "Find the race condition in src/pool.go and fix it."
    messages: list[dict] = [{"role": "user", "content": f"{task}\n\n{BREVITY}"}]

    turn = 0
    advisor_calls = 0
    # {model: {"input": n, "output": n, "cache_read": n}} — advisor and executor
    # bill at different rates, so never sum them together.
    cost = {}

    for turn in range(1, MAX_TURNS + 1):
        capped = advisor_calls >= MAX_ADVISOR_CALLS
        if capped:
            # Remove the tool AND strip the blocks, or the API 400s.
            tools = MY_TOOLS
            messages = strip_advisor_blocks(messages)
        else:
            tools = [advisor_tool(), *MY_TOOLS]

        response = client.beta.messages.create(
            model=EXECUTOR,
            max_tokens=4096,  # executor output only — does NOT bound the advisor
            betas=[BETA],
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )

        for it in response.usage.iterations:
            model = getattr(it, "model", None) or EXECUTOR
            acc = cost.setdefault(model, {"input": 0, "output": 0, "cache_read": 0})
            acc["input"] += it.input_tokens
            acc["output"] += it.output_tokens
            acc["cache_read"] += it.cache_read_input_tokens or 0

        messages.append({"role": "assistant", "content": response.content})
        advisor_calls += count_advisor_calls(response.content)

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":
            # A dangling advisor call: server_tool_use with no result. Re-send the
            # messages unchanged, same tools, same beta. It may pause again.
            continue

        results = run_my_tools(response.content)
        if results:
            messages.append({"role": "user", "content": results})

        if USE_NUDGE and turn == NUDGE_TURN - 1 and advisor_calls == 0:
            # Its own user message, after the tool results. Consecutive user
            # messages are valid and keep the nudge distinct from tool output.
            messages.append({"role": "user", "content": NUDGE_TEXT})

    print(f"\n--- {advisor_calls} advisor call(s) over {turn} turn(s) ---")
    for model, u in cost.items():
        print(
            f"{model:20} in={u['input']:>7,}  cached={u['cache_read']:>7,}  out={u['output']:>7,}"
        )
    print("\nBill each model at its own rate — top-level usage excludes the advisor.")


if __name__ == "__main__":
    main()
