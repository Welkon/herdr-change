#!/bin/sh
entry="${1:?entrypoint}"
ctx_cwd=""
if [ -n "${HERDR_PLUGIN_CONTEXT_JSON:-}" ]; then
  if command -v jq >/dev/null 2>&1; then
    ctx_cwd=$(printf '%s' "$HERDR_PLUGIN_CONTEXT_JSON" | jq -r '.focused_pane_cwd // .workspace_cwd // empty' 2>/dev/null)
  else
    ctx_cwd=$(printf '%s' "$HERDR_PLUGIN_CONTEXT_JSON" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("focused_pane_cwd") or d.get("workspace_cwd") or "")' 2>/dev/null)
  fi
fi
cwd="${HERDR_THREAD_CWD:-${ctx_cwd:-${HERDR_ACTIVE_PANE_CWD:-}}}"
if [ -z "$cwd" ]; then
  cwd=$(herdr pane current 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["cwd"])' 2>/dev/null)
fi
[ -z "$cwd" ] && cwd="$PWD"
printf '%s open %s cwd=%s\n' "$(date +%H:%M:%S)" "$entry" "$cwd" >> /tmp/herdr-thread-plugin.log 2>/dev/null || true
exec herdr plugin pane open --plugin herdr-thread.ui --entrypoint "$entry" \
  --placement popup --focus --cwd "$cwd" --env "HERDR_THREAD_CWD=$cwd"
