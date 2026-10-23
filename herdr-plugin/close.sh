#!/bin/sh
[ -n "${HERDR_THREAD_CWD:-}" ] && cd "$HERDR_THREAD_CWD" 2>/dev/null || true
[ -z "${HERDR_THREAD_CWD:-}" ] && [ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true

result="${TMPDIR:-/tmp}/herdr-thread-ui-close-$$"
/home/felix/.local/bin/herdr-thread ui close --result "$result" 2>>/tmp/herdr-thread-ui.log
ans=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
case "$ans" in
  w) exec /home/felix/.local/bin/herdr-thread close-here --window ;;
  c)
    if ! /home/felix/.local/bin/herdr-thread close-here; then
      printf '\n  Worktree was not removed.\n'
      printf '  Use "Close window only" if you just want to hide it.\n\n'
      printf '  Press Enter to close...'
      read -r _ || true
    fi
    ;;
  *) exit 0 ;;
esac
