#!/bin/sh
[ -n "${HERDR_ACTIVE_PANE_CWD:-}" ] && cd "$HERDR_ACTIVE_PANE_CWD" 2>/dev/null || true
d="$PWD"
emb=""
while [ "$d" != "/" ]; do
  if [ -d "$d/.emb-agent" ]; then emb="--emb-finish"; break; fi
  d=$(dirname "$d")
done

result="${TMPDIR:-/tmp}/herdr-thread-ui-done-$$"
/home/felix/.local/bin/herdr-thread ui done --result "$result" 2>>/tmp/herdr-thread-ui.log
ans=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
case "$ans" in
  p) exec /home/felix/.local/bin/herdr-thread done-here $emb ;;
  r) exec /home/felix/.local/bin/herdr-thread done-here --pr $emb ;;
  *) exit 0 ;;
esac
