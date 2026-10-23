#!/bin/sh
entry="${1:?entrypoint}"
cwd="${HERDR_THREAD_CWD:-${HERDR_ACTIVE_PANE_CWD:-}}"
if [ -z "$cwd" ]; then
  cwd=$(herdr pane current 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["cwd"])' 2>/dev/null)
fi
[ -z "$cwd" ] && cwd="$PWD"
exec herdr plugin pane open --plugin herdr-thread.ui --entrypoint "$entry" \
  --placement popup --focus --cwd "$cwd" --env "HERDR_THREAD_CWD=$cwd"
