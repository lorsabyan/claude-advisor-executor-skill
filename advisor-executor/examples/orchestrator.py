"""Plan big, execute small: Fable 5 coordinator + Sonnet 5 workers.

The coordinator has no tools of its own — it only delegates. Each worker runs in
an isolated context, so the large pages a worker reads never enter anyone else's
context window. That isolation is what makes this cheap.

Cookbook measured ~2.5x cheaper and ~3x faster than a solo frontier agent, with
84-98% of input tokens billed at the worker rate.

Run:  ANTHROPIC_API_KEY=... python orchestrator.py "your hard research question"
"""

import os
import sys

import anthropic

COORDINATOR_MODEL = "claude-fable-5"  # plans, delegates, synthesizes
WORKER_MODEL = "claude-sonnet-5"  # does the token-heavy reading

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def build_team():
    worker = client.beta.agents.create(
        name="search-worker",
        model=WORKER_MODEL,
        tools=[
            {
                "type": "agent_toolset_20260401",
                # Give the worker only what it needs.
                "default_config": {"enabled": False},
                "configs": [
                    {"name": "web_search", "enabled": True},
                    {"name": "web_fetch", "enabled": True},
                ],
            }
        ],
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
        model=COORDINATOR_MODEL,
        tools=[{"type": "agent_toolset_20260401"}],
        multiagent={
            "type": "coordinator",
            # Snapshotted at create time: this pins the worker's CURRENT version.
            # Editing the worker later does nothing until you update the coordinator.
            "agents": [{"type": "agent", "id": worker.id}],
        },
        system=(
            "You are coordinating a team of search workers to answer a hard "
            "web-research question. Your workers have web_search and "
            "web_fetch; you do not. Break the question into focused "
            "sub-questions and delegate each to a worker via create_agent. "
            "Then synthesize their findings into one answer, citing sources."
        ),
    )
    return coordinator, worker


def main():
    question = " ".join(sys.argv[1:]) or (
        "Which three papers introduced the techniques behind modern "
        "retrieval-augmented generation, and what did each contribute?"
    )

    environment = client.beta.environments.create(
        name="research-fanout",
        config={"type": "anthropic_cloud", "networking": {"type": "unrestricted"}},
    )
    coordinator, _worker = build_team()

    session = client.beta.sessions.create(
        agent=coordinator.id,
        environment_id=environment.id,
    )
    client.beta.sessions.events.send(
        session.id,
        events=[
            {
                "type": "user.message",
                "content": [{"type": "text", "text": question}],
            }
        ],
    )

    # The primary thread is a condensed view of everything. You see workers start
    # and finish and any blocking events, but not their full activity — drill into
    # a specific thread with sessions.threads.events.stream() for that.
    with client.beta.sessions.events.stream(session.id) as stream:
        for event in stream:
            match event.type:
                case "session.thread_created":
                    print(f"\n[+] worker thread: {event.agent_name}")
                case "agent.thread_message_sent":
                    print(f"\n[>] delegated to {event.to_agent_name}")
                case "agent.thread_message_received":
                    print(f"\n[<] {event.from_agent_name} reported back")
                case "agent.message":
                    for block in event.content:
                        if block.type == "text":
                            print(block.text, end="", flush=True)
                case "session.status_idle":
                    break  # all threads idle — the coordinator is done

    for thread in client.beta.sessions.threads.list(session.id):
        print(f"\n[{thread.agent.name}] {thread.status}")


if __name__ == "__main__":
    main()
