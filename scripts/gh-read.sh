#!/usr/bin/env bash
# gh-read: GET-only GitHub API wrapper (ChatGPT finding #2 fix).
# Enforces read-only at runtime: any non-GET method is refused before
# any network call. Cron sensors must call gh through this wrapper.
# Usage: gh-read <gh api args... >  (must include -X GET or default GET)
set -euo pipefail
for arg in "$@"; do
  case "$arg" in
    -X|--method)
      echo "gh-read: explicit method flag forbidden (GET only)" >&2
      exit 3
      ;;
    -XPOST|-XPUT|-XPATCH|-XDELETE|-XHEAD)
      echo "gh-read: write method refused: $arg" >&2
      exit 3
      ;;
    -F|--field|-f|--raw-field|--input)
      echo "gh-read: body-field flags forbidden (GET only): $arg" >&2
      exit 3
      ;;
  esac
done
# Reject --method values and -F/--field writes smuggled as POST inference.
if printf '%s\n' "$@" | grep -q -E '^(-X\s*(POST|PUT|PATCH|DELETE)|--method)'; then
  echo "gh-read: non-GET method refused" >&2
  exit 3
fi
exec gh api --method GET "$@"
