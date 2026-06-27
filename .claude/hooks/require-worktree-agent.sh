#!/bin/bash
# PreToolUse hook for Agent: blocks development agents on main.
# Forces entering a worktree so analysis and edits happen in the same context.

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git branch --show-current 2>/dev/null)
[ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ] && exit 0

INPUT=$(cat)
SUBAGENT=$(echo "$INPUT" | grep -oP '"subagent_type"\s*:\s*"\K[^"]*' || true)

# Block development agents on main (read-only agents are allowed)
case "$SUBAGENT" in
  feature-dev:code-explorer|feature-dev:code-reviewer)
    exit 0 ;;  # read-only — no Edit/Write tools
  Plan|feature-dev:*)
    cat >&2 <<'EOF'
BLOCKED: Development/architecture agent on the main branch.
Use EnterWorktree before running analysis or development agents.
This ensures analysis and subsequent changes happen in the same worktree.
EOF
    exit 2
    ;;
esac

exit 0
