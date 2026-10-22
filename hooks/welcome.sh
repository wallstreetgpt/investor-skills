#!/usr/bin/env bash
# Investor Skills welcome. Runs when a Claude Code session starts. Until setup is done
# (the SEC contact saved in ~/.investor/config.json), it shows the user a one-line hint and
# asks Claude to welcome them and offer setup. After setup it prints nothing.
config="${INVESTOR_HOME:-$HOME/.investor}/config.json"
if [ -f "$config" ] && grep -q '"sec_user_agent"' "$config"; then
  exit 0
fi
cat "$(dirname "$0")/welcome.json"
