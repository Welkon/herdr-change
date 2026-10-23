#!/bin/sh
[ -n "${HERDR_THREAD_CWD:-}" ] && cd "$HERDR_THREAD_CWD" 2>/dev/null || true
[ -z "${HERDR_THREAD_CWD:-}" ] && [ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true

printf '%s task cwd=%s\n' "$(date +%H:%M:%S)" "$PWD" >> /tmp/herdr-thread-plugin.log 2>/dev/null || true

# Refuse before showing the task UI in emb-agent projects.
d="$PWD"
while [ "$d" != "/" ]; do
  if [ -d "$d/.emb-agent" ] && { [ -f "$d/.emb-agent/project.json" ] || [ -f "$d/.emb-agent/config.yaml" ] || [ -f "$d/.emb-agent/.current-task" ]; }; then
    printf '\n  emb-agent project: adopting current task...\n\n'
    if ! /home/felix/.local/bin/herdr-thread emb adopt --focus; then
      printf '\n  Could not adopt. Use: herdr-thread emb status\n\n'
      printf '  Press Enter to close...'
      read -r _ || true
    fi
    exit 0
  fi
  d=$(dirname "$d")
done

result="${TMPDIR:-/tmp}/herdr-thread-ui-task-$$"
/home/felix/.local/bin/herdr-thread ui task --result "$result" 2>>/tmp/herdr-thread-ui.log
task=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
[ -n "$task" ] && exec /home/felix/.local/bin/herdr-thread task "$task"
exit 0
