#!/bin/sh
result="${TMPDIR:-/tmp}/herdr-thread-ui-task-$$"
/home/felix/.local/bin/herdr-thread ui task --result "$result" 2>>/tmp/herdr-thread-ui.log
task=$(cat "$result" 2>/dev/null || true)
rm -f "$result"
[ -n "$task" ] && exec /home/felix/.local/bin/herdr-thread task "$task"
exit 0
