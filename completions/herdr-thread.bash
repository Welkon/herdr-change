_herdr_thread() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  local cmds="new list show setup transcript turn search capture prompt review checkpoint land pr cleanup open diff tag sync recover watch fork subagent join comment attach send machines remote runner share notify backup prune doctor"
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=( $(compgen -W "$cmds" -- "$cur") )
    return
  fi
  case "$prev" in
    --strategy) COMPREPLY=( $(compgen -W "push pr merge merge-pr hook" -- "$cur") );;
    --profile) COMPREPLY=( $(compgen -W "worker scout reviewer" -- "$cur") );;
    --method) COMPREPLY=( $(compgen -W "merge squash rebase" -- "$cur") );;
    runner) COMPREPLY=( $(compgen -W "list add up down bootstrap forward unforward" -- "$cur") );;
  esac
}
complete -F _herdr_thread herdr-thread
