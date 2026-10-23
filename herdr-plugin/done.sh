#!/bin/sh
[ -n "${HERDR_CHANGE_CWD:-}" ] && cd "$HERDR_CHANGE_CWD" 2>/dev/null || true
[ -z "${HERDR_CHANGE_CWD:-}" ] && [ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true
d="$PWD"
emb=""
while [ "$d" != "/" ]; do
  if [ -d "$d/.emb-agent" ] && { [ -f "$d/.emb-agent/project.json" ] || [ -f "$d/.emb-agent/config.yaml" ] || [ -f "$d/.emb-agent/.current-task" ]; }; then emb="--emb-finish"; break; fi
  d=$(dirname "$d")
done
BIN="${HERDR_CHANGE_BIN:-$(command -v herdr-change || echo "$HOME/.local/bin/herdr-change")}"

result="${TMPDIR:-/tmp}/herdr-change-ui-done-$$"
"$BIN" ui done --result "$result" 2>>/tmp/herdr-change-ui.log
ans=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
case "$ans" in
  p) exec "$BIN" done-here $emb ;;
  r) exec "$BIN" done-here --pr $emb ;;
  *) exit 0 ;;
esac
