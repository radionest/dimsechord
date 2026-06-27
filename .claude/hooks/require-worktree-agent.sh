#!/bin/bash
# PreToolUse hook for Agent: only read-only agents may run on the main branch.
# Any agent that can mutate files must run inside a worktree, so analysis and
# edits happen in the same context.

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git branch --show-current 2>/dev/null)
[ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ] && exit 0

INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  SUBAGENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty')
else
  SUBAGENT=$(printf '%s' "$INPUT" | grep -oP '"subagent_type"\s*:\s*"\K[^"]*' || true)
fi

# Allowlist: read-only agents (no Edit/Write/NotebookEdit) may run on main.
# Everything else — including write-capable general-purpose agents — is blocked,
# so a write-capable agent can never run on main by default (fail-closed).
case "$SUBAGENT" in
  Explore|Plan|feature-dev:code-explorer|feature-dev:code-reviewer|claude-code-guide)
    exit 0
    ;;
esac

cat >&2 <<'EOF'
BLOCKED: This agent can modify files and may not run on the main branch.
Use EnterWorktree before launching development or architecture agents.
This ensures analysis and subsequent changes happen in the same worktree.
(Read-only agents such as Explore/Plan/code-explorer/code-reviewer are allowed.)
EOF
exit 2
