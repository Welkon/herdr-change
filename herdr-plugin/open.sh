#!/bin/sh
entry="${1:?entrypoint}"
exec herdr plugin pane open --plugin herdr-thread.ui --entrypoint "$entry" \
  --placement popup --focus --cwd "${HERDR_ACTIVE_PANE_CWD:-$PWD}"
