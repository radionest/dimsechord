#!/bin/bash
# PostToolUse hook: detect `gh pr create`, start background monitoring.

INPUT=$(cat)

echo "$INPUT" | grep -q "gh pr create" || exit 0
echo "$INPUT" | grep -qE "(--help|--dry-run)" && exit 0

PR_URL=$(echo "$INPUT" | grep -oP 'https://github\.com/[^"]+/pull/\d+' | head -1)
[ -z "$PR_URL" ] && exit 0

PR_NUM=$(echo "$PR_URL" | grep -oP '\d+$')
REPORT="/tmp/pr-${PR_NUM}-report.md"
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

nohup "$HOOK_DIR/pr-watch.sh" "$PR_NUM" 12 300 > /dev/null 2>&1 &

cat >&2 <<EOF
PR_CREATED: PR #${PR_NUM} (${PR_URL}).
Background CI monitor started (PID $!, polling every 5 min, max 1 hour).
Report: ${REPORT}
When the user asks about PR status, read ${REPORT}.
EOF

exit 2
