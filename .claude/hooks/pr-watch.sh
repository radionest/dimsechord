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
    # Poll before sleeping so an already-finished CI is reported immediately.
    # gh pr checks exit codes: 0=all pass, 1=some failed, 8=pending, other=error.
    if CHECKS=$(gh pr checks "$PR_NUM" 2>&1); then RC=0; else RC=$?; fi
    STATUS_JSON=$(gh pr view "$PR_NUM" --json statusCheckRollup,comments,reviews,state 2>&1) || true

    # Only treat an authoritative pass(0)/fail(1) as terminal; pending(8) and any
    # gh error keep us waiting instead of writing a false "complete" report.
    if [ "$RC" -eq 0 ] || [ "$RC" -eq 1 ]; then
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

    echo "Iteration ${i}/${MAX_ITER}: checks not final (gh rc=${RC}, $(date -Iseconds))" >> "$REPORT"
    sleep "$INTERVAL"
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
