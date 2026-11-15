#!/usr/bin/env bash
# The registry's current index, read through the API and not from the public download address:
# that one is cached for minutes, and a release building on a list from before the previous
# release would drop that release from it. No registry yet: nothing to read, start a new list.
# Any other failure stops the release, since starting empty would drop every release listed.
set -euo pipefail
out="$1"
mkdir -p "$(dirname "$out")"
if ! gh release view registry >/dev/null 2>&1; then
  echo "No registry release yet: a new index."
  exit 0
fi
id=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/registry" \
  --jq '.assets[] | select(.name == "index.json") | .id')
if [ -z "$id" ]; then
  echo "The registry has no index yet: a new one."
  exit 0
fi
gh api -H "Accept: application/octet-stream" "repos/$GITHUB_REPOSITORY/releases/assets/$id" > "$out"
python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$out"
echo "Read the index ($(wc -c < "$out") bytes)."
