#!/bin/sh
# End-to-end smoke test for herdr-change. Uses an isolated DB/config/worktree root.
set -eu

ROOT="/tmp/herdr-change-smoke-$$"
DATA="$ROOT/data"
CFG="$ROOT/config.toml"
WTROOT="$ROOT/worktrees"
FAKEBIN="$ROOT/bin"
mkdir -p "$DATA" "$WTROOT" "$FAKEBIN"

cat > "$CFG" <<EOF
worktree_root = "$WTROOT"
default_agent = "codex"
land_strategy = "push"
notify = false
EOF

export HERDR_CHANGE_DATA="$DATA"
export HERDR_CHANGE_CONFIG="$CFG"
export PATH="$FAKEBIN:$PATH"

IDS=""
cleanup() {
  for id in $IDS; do herdr-change cleanup "$id" >/dev/null 2>&1 || true; done
  rm -rf "$ROOT"
}
trap cleanup EXIT INT TERM

new_change() {
  out=$(herdr-change new "$1" --repo "$2" --worktree "$3" --no-agent)
  echo "$out" | sed -n 's/^change \([0-9a-f]*\):.*/\1/p'
}

make_repo() {
  d=$1
  mkdir -p "$d"
  (cd "$d" && git init -q -b main && git config user.email t@t && git config user.name t \
    && echo base > a.txt && git add . && git commit -qm init)
}

echo "== batch push =="
make_repo "$ROOT/r1"
git init -q --bare "$ROOT/r1.git"
git -C "$ROOT/r1" remote add origin "$ROOT/r1.git"
ID1=$(new_change "smoke one" "$ROOT/r1" "$ROOT/wt1"); IDS="$IDS $ID1"
ID2=$(new_change "smoke two" "$ROOT/r1" "$ROOT/wt2"); IDS="$IDS $ID2"
echo one >> "$ROOT/wt1/a.txt"; herdr-change checkpoint "$ID1" -m one >/dev/null
echo two >> "$ROOT/wt2/a.txt"; herdr-change checkpoint "$ID2" -m two >/dev/null
herdr-change land --all --strategy push --dry-run >/dev/null
herdr-change land --all --strategy push --yes --cleanup >/dev/null
[ "$(git --git-dir="$ROOT/r1.git" for-each-ref --format='%(refname:short)' refs/heads | wc -l)" -eq 2 ]

echo "== multi repo =="
make_repo "$ROOT/m1"; make_repo "$ROOT/m2"
git init -q --bare "$ROOT/m1.git"; git init -q --bare "$ROOT/m2.git"
git -C "$ROOT/m1" remote add origin "$ROOT/m1.git"
git -C "$ROOT/m2" remote add origin "$ROOT/m2.git"
MID=$(herdr-change new "smoke multi" --repo "$ROOT/m1" --repo "$ROOT/m2" --no-agent | sed -n 's/^change \([0-9a-f]*\):.*/\1/p')
IDS="$IDS $MID"
WTS=$(herdr-change show "$MID" --json | python3 -c 'import json,sys;print(" ".join(r["worktree"] for r in json.load(sys.stdin)["repos"]))')
for w in $WTS; do echo x >> "$w/a.txt"; done
herdr-change checkpoint "$MID" -m multi >/dev/null
herdr-change land "$MID" --strategy push >/dev/null
[ "$(git --git-dir="$ROOT/m1.git" for-each-ref --format='%(refname:short)' refs/heads | wc -l)" -eq 1 ]
[ "$(git --git-dir="$ROOT/m2.git" for-each-ref --format='%(refname:short)' refs/heads | wc -l)" -eq 1 ]

echo "== checkpoint restore =="
make_repo "$ROOT/ck"
CID=$(new_change "smoke ck" "$ROOT/ck" "$ROOT/wtck"); IDS="$IDS $CID"
echo 1 >> "$ROOT/wtck/a.txt"; herdr-change checkpoint "$CID" -m c1 >/dev/null
C1=$(herdr-change checkpoint "$CID" --list | head -1 | awk '{print $1}')
echo 2 >> "$ROOT/wtck/a.txt"; herdr-change checkpoint "$CID" -m c2 >/dev/null
herdr-change checkpoint "$CID" --restore "$C1" >/dev/null
grep -q '^1$' "$ROOT/wtck/a.txt"
! grep -q '^2$' "$ROOT/wtck/a.txt"

echo "== turns/comments/attach =="
herdr-change turn "$CID" --add "note" >/dev/null
herdr-change search smoke >/dev/null
herdr-change comment "$CID" "check" --file a.txt --line 1 >/dev/null
herdr-change attach "$CID" "$ROOT/wtck/a.txt" >/dev/null
[ -f "$ROOT/wtck/.herdr-change/attachments/a.txt" ]

echo "== PR + merge-pr =="
make_repo "$ROOT/pr"; git init -q --bare "$ROOT/pr.git"
git -C "$ROOT/pr" remote add origin "$ROOT/pr.git"
cat > "$FAKEBIN/gh" <<'EOF'
#!/bin/sh
case "$1 $2" in
  "pr create") echo "https://github.com/acme/repo/pull/1"; exit 0 ;;
  "pr view") echo '{"state":"OPEN","isDraft":false,"mergeable":"MERGEABLE","reviewDecision":"APPROVED","url":"https://github.com/acme/repo/pull/1","number":1,"title":"x","headRefName":"x","baseRefName":"main","statusCheckRollup":[]}'; exit 0 ;;
  "pr merge") exit 0 ;;
esac
exit 1
EOF
chmod +x "$FAKEBIN/gh"
PID=$(new_change "smoke pr" "$ROOT/pr" "$ROOT/wtpr"); IDS="$IDS $PID"
echo p >> "$ROOT/wtpr/a.txt"; herdr-change checkpoint "$PID" -m pr >/dev/null
herdr-change land "$PID" --strategy pr >/dev/null
herdr-change pr "$PID" >/dev/null
herdr-change land "$PID" --strategy merge-pr --method squash >/dev/null
herdr-change show "$PID" | grep -q 'status      landed'

echo "== recover =="
WS=$(herdr-change show "$CID" | sed -n 's/.*workspace   \([^ ]*\).*/\1/p')
herdr workspace close "$WS" >/dev/null
herdr-change recover --create >/dev/null
herdr-change show "$CID" | grep -q "workspace"

echo "SMOKE OK"
