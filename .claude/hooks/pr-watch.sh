#!/bin/bash
# Background PR monitor — polls GitHub CI checks and writes report.
# Usage: pr-watch.sh <PR_NUMBER> [max_iterations] [interval_seconds]

set -euo pipefail

PR_NUM="${1:?Usage: pr-watch.sh <PR_NUMBER>}"
MAX_ITER="${2:-12}"
INTERVAL="${3:-300}"
REPORT="/tmp/pr-${PR_NUM}-report.md"

echo "# PR #${PR_NUM} — Monitoring started $(date -Iseconds)" > "$REPORT"
echo "Checking every ${INTERVAL}s, max ${MAX_ITER} iterations." >> "$REPORT"

for ((i=1; i<=MAX_ITER; i++)); do
    sleep "$INTERVAL"

    CHECKS=$(gh pr checks "$PR_NUM" 2>&1) || true
    STATUS_JSON=$(gh pr view "$PR_NUM" --json statusCheckRollup,comments,reviews,state 2>&1) || true

    PENDING=$(echo "$CHECKS" | grep -c "pending\|queued\|in_progress" || true)

    if [ "$PENDING" -eq 0 ]; then
        cat > "$REPORT" <<REPORT_EOF
# PR #${PR_NUM} — CI Complete ($(date -Iseconds))

## Check Results
\`\`\`
${CHECKS}
\`\`\`

## Reviews & Comments
\`\`\`json
${STATUS_JSON}
\`\`\`
REPORT_EOF
        exit 0
    fi

    echo "Iteration ${i}/${MAX_ITER}: ${PENDING} checks pending ($(date -Iseconds))" >> "$REPORT"
done

cat > "$REPORT" <<REPORT_EOF
# PR #${PR_NUM} — Monitoring Timeout ($(date -Iseconds))

Gave up after ${MAX_ITER} iterations ($(( MAX_ITER * INTERVAL / 60 )) minutes).

## Last Check Results
\`\`\`
${CHECKS:-no data}
\`\`\`

## Reviews & Comments
\`\`\`json
${STATUS_JSON:-no data}
\`\`\`
REPORT_EOF
