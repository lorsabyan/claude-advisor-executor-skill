#!/usr/bin/env python3
"""Validate an executor/advisor model pair against the advisor-tool matrix.

The advisor must be Sonnet 4.6 or more capable, AND at least as capable as the
executor. Equal-capability models may advise each other. Invalid pairs are a 400
at request time — check here instead.

  ./validate_pair.py claude-sonnet-5 claude-fable-5   -> ok
  ./validate_pair.py claude-fable-5 claude-sonnet-5   -> invalid, lists valid advisors
  ./validate_pair.py --list                           -> print the whole matrix

Matrix as documented 2026-07. If Anthropic ships new models, re-check the docs:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool#model-compatibility
"""

import sys

FRONTIER = ["claude-fable-5", "claude-mythos-5", "claude-opus-4-8", "claude-opus-4-7"]

MATRIX: dict[str, list[str]] = {
    "claude-haiku-4-5": FRONTIER + ["claude-opus-4-6", "claude-sonnet-4-6"],
    "claude-sonnet-4-6": FRONTIER + ["claude-opus-4-6", "claude-sonnet-4-6"],
    "claude-sonnet-5": FRONTIER,
    "claude-opus-4-6": FRONTIER + ["claude-opus-4-6"],
    "claude-opus-4-7": FRONTIER,
    "claude-opus-4-8": FRONTIER,
    "claude-fable-5": ["claude-fable-5"],
    "claude-mythos-5": ["claude-mythos-5"],
}


def print_matrix() -> None:
    width = max(len(m) for m in MATRIX)
    print(f"{'EXECUTOR'.ljust(width)}  VALID ADVISORS")
    for executor, advisors in MATRIX.items():
        print(f"{executor.ljust(width)}  {', '.join(advisors)}")


def main() -> int:
    args = sys.argv[1:]

    if "--list" in args or not args:
        print_matrix()
        return 0

    if len(args) != 2:
        print("usage: validate_pair.py <executor> <advisor>", file=sys.stderr)
        print("       validate_pair.py --list", file=sys.stderr)
        return 2

    executor, advisor = args

    if executor not in MATRIX:
        print(f"✗ unknown executor: {executor}", file=sys.stderr)
        print(f"  known: {', '.join(MATRIX)}", file=sys.stderr)
        return 2

    valid = MATRIX[executor]
    if advisor in valid:
        print(f"✓ {executor} (executor) + {advisor} (advisor) is a valid pair")
        return 0

    print(f"✗ {advisor} cannot advise {executor} — the API will return a 400.")
    print(f"  Valid advisors for {executor}: {', '.join(valid)}")
    if advisor not in MATRIX and advisor not in {a for v in MATRIX.values() for a in v}:
        print(f"  ({advisor} is not a recognized model ID.)")
    else:
        print("  The advisor must be at least as capable as the executor.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
