#!/usr/bin/env bash
# Claude Usage Checker — sources credentials from config.env in same dir
# Run: bash claude_usage.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.env"

RESPONSE=$(curl -s "https://claude.ai/api/organizations/${ORG_ID}/usage" \
  -H 'accept: */*' \
  -H 'anthropic-client-platform: web_claude_ai' \
  -H 'anthropic-client-version: 1.0.0' \
  -H 'content-type: application/json' \
  -H 'referer: https://claude.ai/settings/usage' \
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36' \
  -b "sessionKey=${SESSION_KEY}; cf_clearance=${CF_CLEARANCE}")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "        Claude Usage (raw JSON)      "
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if command -v jq &>/dev/null; then
  echo "$RESPONSE" | jq .
else
  echo "$RESPONSE"
  echo ""
  echo "(Install 'jq' for prettier output: sudo apt install jq)"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
