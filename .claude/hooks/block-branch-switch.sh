#!/bin/bash
# PreToolUse hook for Bash: blocks branch switching in the root project directory.
# Use EnterWorktree to work on a different branch.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | grep -oP '"command"\s*:\s*"\K[^"]*' || true)
[ -z "$COMMAND" ] && exit 0

# Check for git checkout/switch (not file-restore variant)
if ! echo "$COMMAND" | grep -qP 'git\s+(checkout|switch)\b'; then
  exit 0
fi

# Allow git checkout -- <file> (restore files)
if echo "$COMMAND" | grep -qP 'git\s+(checkout|switch)\s+--\s'; then
  exit 0
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Check that we're in the root directory (not a worktree)
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null)
COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null)

if [ "$(realpath "$GIT_DIR")" = "$(realpath "$COMMON_DIR")" ]; then
  cat >&2 <<'EOF'
BLOCKED: Branch switching in the root project directory is not allowed.
Use EnterWorktree to work on a different branch.
`git checkout -- <file>` for restoring files is still available.
EOF
  exit 2
fi

exit 0
