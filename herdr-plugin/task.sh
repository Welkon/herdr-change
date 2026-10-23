#!/bin/sh
[ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true

# Refuse before showing the task UI in emb-agent projects.
d="$PWD"
while [ "$d" != "/" ]; do
  if [ -d "$d/.emb-agent" ]; then
    printf '\n  This is an emb-agent project.\n\n'
    printf '  Use:  herdr-thread emb adopt\n\n'
    printf '  herdr-thread will not create a second worktree here.\n\n'
    printf '  Press Enter to close...'
    read -r _ || true
    exit 1
  fi
  d=$(dirname "$d")
done

result="${TMPDIR:-/tmp}/herdr-thread-ui-task-$$"
/home/felix/.local/bin/herdr-thread ui task --result "$result" 2>>/tmp/herdr-thread-ui.log
task=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
[ -n "$task" ] && exec /home/felix/.local/bin/herdr-thread task "$task"
exit 0
