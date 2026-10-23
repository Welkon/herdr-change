#!/bin/sh
[ -n "${HERDR_CHANGE_CWD:-}" ] && cd "$HERDR_CHANGE_CWD" 2>/dev/null || true
[ -z "${HERDR_CHANGE_CWD:-}" ] && [ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true
BIN="${HERDR_CHANGE_BIN:-$(command -v herdr-change || echo "$HOME/.local/bin/herdr-change")}"

printf '%s task cwd=%s\n' "$(date +%H:%M:%S)" "$PWD" >> /tmp/herdr-change-plugin.log 2>/dev/null || true

# Refuse before showing the task UI in emb-agent projects.
d="$PWD"
while [ "$d" != "/" ]; do
  if [ -d "$d/.emb-agent" ] && { [ -f "$d/.emb-agent/project.json" ] || [ -f "$d/.emb-agent/config.yaml" ] || [ -f "$d/.emb-agent/.current-task" ]; }; then
    printf '\n  emb-agent project: adopting current task...\n\n'
    if ! "$BIN" emb adopt --focus; then
      printf '\n  Could not adopt. Use: herdr-change emb status\n\n'
      printf '  Press Enter to close...'
      read -r _ || true
    fi
    exit 0
  fi
  d=$(dirname "$d")
done

result="${TMPDIR:-/tmp}/herdr-change-ui-task-$$"
"$BIN" ui task --result "$result" 2>>/tmp/herdr-change-ui.log
task=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
[ -n "$task" ] && exec "$BIN" task "$task"
exit 0
