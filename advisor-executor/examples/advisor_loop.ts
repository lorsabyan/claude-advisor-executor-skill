/**
 * Complete advisor-tool agent loop: Sonnet 5 executor + Fable 5 advisor.
 *
 * Covers pause_turn resumption, verbatim round-tripping of advisor_tool_result
 * blocks, a conversation-level call cap, the turn-2 nudge, and per-model cost
 * accounting from usage.iterations (top-level usage is executor-only).
 *
 * Run:  ANTHROPIC_API_KEY=... npx tsx advisor_loop.ts
 */

import Anthropic from "@anthropic-ai/sdk";

const EXECUTOR = "claude-sonnet-5";
const ADVISOR = "claude-fable-5";
const BETA = "advisor-tool-2026-03-01";

const MAX_TURNS = 10;
const MAX_ADVISOR_CALLS = 3; // conversation-level cap; the API only caps per-request

// The nudge helps Haiku (~+7pts), does nothing for Sonnet, and HURTS Opus.
// Tune NUDGE_TURN against your executor's baseline first-call turn before shipping.
const NUDGE_TURN = 2;
const NUDGE_TEXT =
  "You have not consulted the advisor yet. If the task has a non-obvious " +
  "design decision or a failure mode you haven't ruled out, call advisor " +
  "now before committing to an approach.";
const USE_NUDGE = EXECUTOR.startsWith("claude-haiku");

// Soft brevity request, addressed to the advisor directly — it reads user
// messages as quoted context and follows second-person instructions far more
// reliably than third-person ones. Ask for ~80% of your true ceiling.
const BREVITY =
  "(Advisor: please keep your guidance under 80 words — I need a focused starting point, not a comprehensive plan.)";

const SYSTEM = `You are a coding agent.

You have access to an \`advisor\` tool backed by a stronger reviewer model. It takes NO parameters — when you call advisor(), your entire conversation history is automatically forwarded.

Call advisor BEFORE substantive work — before writing, before committing to an interpretation, before building on an assumption. Orientation (finding files, fetching a source) is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor when you believe the task is complete (make your deliverable durable first), when stuck, and when considering a change of approach.

Give the advice serious weight. A passing self-test is not evidence the advice is wrong — it's evidence your test doesn't check what the advice is checking.`;

const advisorTool = (): Anthropic.Beta.Messages.BetaToolUnion => ({
  type: "advisor_20260301",
  name: "advisor",
  model: ADVISOR,
  // Highest-leverage cost setting. Uncapped advisors emit ~4-6k tokens on hard
  // tasks; 2048 cuts that ~7x with near-zero truncation. Minimum is 1024.
  max_tokens: 2048,
  // Advisor-side cache breaks even at ~3 calls/conversation. Set once — toggling
  // mid-conversation causes misses.
  ...(MAX_ADVISOR_CALLS >= 3
    ? { caching: { type: "ephemeral" as const, ttl: "5m" as const } }
    : {}),
});

const MY_TOOLS: Anthropic.Beta.Messages.BetaToolUnion[] = [
  {
    name: "run_bash",
    description: "Run a bash command in the repo.",
    input_schema: {
      type: "object",
      properties: { command: { type: "string" } },
      required: ["command"],
    },
  },
];

/** Replace with real dispatch. One tool_result per tool_use block. */
function runMyTools(
  content: Anthropic.Beta.Messages.BetaContentBlock[],
): Anthropic.Beta.Messages.BetaToolResultBlockParam[] {
  return content
    .filter((b) => b.type === "tool_use")
    .map((b) => ({
      type: "tool_result" as const,
      tool_use_id: b.id,
      content: `(stub output for: ${JSON.stringify(b.input)})`,
    }));
}

function countAdvisorCalls(
  content: Anthropic.Beta.Messages.BetaContentBlock[],
): number {
  return content.filter(
    (b) => b.type === "server_tool_use" && b.name === "advisor",
  ).length;
}

/**
 * Required before dropping the advisor tool: leaving advisor_tool_result blocks
 * in the history with no advisor tool in `tools` is a 400.
 */
function stripAdvisorBlocks(
  messages: Anthropic.Beta.Messages.BetaMessageParam[],
): Anthropic.Beta.Messages.BetaMessageParam[] {
  // Match by name, not just type: other server tools (web_search, ...) also emit
  // server_tool_use blocks, and stripping those would corrupt their history.
  const isAdvisorBlock = (b: any) =>
    (b.type === "server_tool_use" && b.name === "advisor") ||
    b.type === "advisor_tool_result";
  return messages.flatMap((msg) => {
    if (!Array.isArray(msg.content)) return [msg];
    const content = msg.content.filter((b: any) => !isAdvisorBlock(b));
    return content.length ? [{ ...msg, content }] : [];
  });
}

async function main() {
  const client = new Anthropic();

  const task = "Find the race condition in src/pool.go and fix it.";
  let messages: Anthropic.Beta.Messages.BetaMessageParam[] = [
    { role: "user", content: `${task}\n\n${BREVITY}` },
  ];

  let advisorCalls = 0;
  // Advisor and executor bill at different rates — never sum them together.
  const cost: Record<
    string,
    { input: number; output: number; cacheRead: number }
  > = {};

  let turn = 0;
  for (turn = 1; turn <= MAX_TURNS; turn++) {
    const capped = advisorCalls >= MAX_ADVISOR_CALLS;
    let tools: Anthropic.Beta.Messages.BetaToolUnion[];
    if (capped) {
      // Remove the tool AND strip the blocks, or the API 400s.
      tools = MY_TOOLS;
      messages = stripAdvisorBlocks(messages);
    } else {
      tools = [advisorTool(), ...MY_TOOLS];
    }

    const response = await client.beta.messages.create({
      model: EXECUTOR,
      max_tokens: 4096, // executor output only — does NOT bound the advisor
      betas: [BETA],
      system: SYSTEM,
      tools,
      messages,
    });

    for (const it of response.usage.iterations ?? []) {
      const model = (it as any).model ?? EXECUTOR;
      const acc = (cost[model] ??= { input: 0, output: 0, cacheRead: 0 });
      acc.input += it.input_tokens ?? 0;
      acc.output += it.output_tokens ?? 0;
      acc.cacheRead += it.cache_read_input_tokens ?? 0;
    }

    messages.push({ role: "assistant", content: response.content });
    advisorCalls += countAdvisorCalls(response.content);

    if (response.stop_reason === "end_turn") break;

    if (response.stop_reason === "pause_turn") {
      // Dangling advisor call: server_tool_use with no result. Re-send the
      // messages unchanged, same tools, same beta. It may pause again.
      continue;
    }

    const results = runMyTools(response.content);
    if (results.length) messages.push({ role: "user", content: results });

    if (USE_NUDGE && turn === NUDGE_TURN - 1 && advisorCalls === 0) {
      // Its own user message, after the tool results. Consecutive user messages
      // are valid and keep the nudge distinct from tool output.
      messages.push({ role: "user", content: NUDGE_TEXT });
    }
  }

  console.log(`\n--- ${advisorCalls} advisor call(s) over ${turn} turn(s) ---`);
  for (const [model, u] of Object.entries(cost)) {
    console.log(
      `${model.padEnd(20)} in=${u.input}  cached=${u.cacheRead}  out=${u.output}`,
    );
  }
  console.log(
    "\nBill each model at its own rate — top-level usage excludes the advisor.",
  );
}

main();
