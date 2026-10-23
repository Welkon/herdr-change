#!/bin/sh
[ -n "${HERDR_CHANGE_CWD:-}" ] && cd "$HERDR_CHANGE_CWD" 2>/dev/null || true
[ -z "${HERDR_CHANGE_CWD:-}" ] && [ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true
BIN="${HERDR_CHANGE_BIN:-$(command -v herdr-change || echo "$HOME/.local/bin/herdr-change")}"

result="${TMPDIR:-/tmp}/herdr-change-ui-close-$$"
"$BIN" ui close --result "$result" 2>>/tmp/herdr-change-ui.log
ans=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
case "$ans" in
  w) exec "$BIN" close-here --window ;;
  c)
    if ! "$BIN" close-here; then
      printf '\n  Worktree was not removed.\n'
      printf '  Use "Close window only" if you just want to hide it.\n\n'
      printf '  Press Enter to close...'
      read -r _ || true
    fi
    ;;
  *) exit 0 ;;
esac
