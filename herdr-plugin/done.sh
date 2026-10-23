#!/bin/sh
[ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true
result="${TMPDIR:-/tmp}/herdr-thread-ui-done-$$"
/home/felix/.local/bin/herdr-thread ui done --result "$result" 2>>/tmp/herdr-thread-ui.log
ans=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
case "$ans" in
  p) exec /home/felix/.local/bin/herdr-thread done-here ;;
  r) exec /home/felix/.local/bin/herdr-thread done-here --pr ;;
  *) exit 0 ;;
esac
