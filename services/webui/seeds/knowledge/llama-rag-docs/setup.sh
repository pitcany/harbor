#!/usr/bin/env bash
# One-shot ingest for the `ai-stack-runbook` Knowledge collection.
#
# Prerequisites:
#   1) Ollama running on the host (default: http://172.17.0.1:11434) AND
#      has nomic-embed-text pulled.  Test:
#        curl -s http://localhost:11434/api/tags | grep nomic-embed-text
#   2) A WebUI API key for the admin user (Account → Settings → Account → API Keys → New).
#      Export it before running:  export WEBUI_API_KEY=...
#
# What this script does:
#   - Reads ./manifest.json (the collection definition + source file list)
#   - Creates the Knowledge collection in WebUI via /api/v1/knowledge/create
#   - For each source: uploads the file via /api/v1/files/ then adds it to
#     the collection via /api/v1/knowledge/{id}/file/add  (triggers embedding)
#   - Prints the collection id at the end so you can paste it into
#     seeds/models.json under llama-rag's meta.knowledge
#
# Safe to re-run: if the collection name already exists, the script exits
# without modifying it.  Delete it in WebUI first to re-ingest.

set -euo pipefail

WEBUI_URL="${WEBUI_URL:-http://localhost:33801}"
WEBUI_API_KEY="${WEBUI_API_KEY:?WEBUI_API_KEY not set — mint one in WebUI → Account → API Keys}"
MANIFEST="$(dirname "$0")/manifest.json"

# Sanity-check the embedding backend before doing anything else.
echo "[seed-knowledge] checking embedding backend…"
embed_engine=$(jq -r .embedding_engine "$MANIFEST")
embed_model=$(jq -r .embedding_model "$MANIFEST")
if [ "$embed_engine" = "ollama" ]; then
  ollama_url="${OLLAMA_URL:-http://localhost:11434}"
  if ! curl -s -f --max-time 3 "$ollama_url/api/tags" >/dev/null; then
    echo "[seed-knowledge] FATAL: ollama at $ollama_url is unreachable." >&2
    echo "  Start it with: sudo systemctl start ollama" >&2
    exit 1
  fi
  if ! curl -s "$ollama_url/api/tags" | jq -e --arg m "$embed_model" \
       '.models | map(.name) | any(. == $m or . == ($m | sub(":latest"; "")))' >/dev/null; then
    echo "[seed-knowledge] FATAL: model '$embed_model' not pulled in ollama." >&2
    echo "  Pull it with: ollama pull ${embed_model%:latest}" >&2
    exit 1
  fi
fi

# Auth header for every curl below.
AUTH="Authorization: Bearer $WEBUI_API_KEY"
JSON="Content-Type: application/json"

# Check if the collection already exists.
name=$(jq -r .name "$MANIFEST")
desc=$(jq -r .description "$MANIFEST")
# Note: /api/v1/knowledge/ returns {"items":[…], "total":N}, not a bare array.
# (.items // .) handles both shapes — wrapped object or bare array.
existing=$(curl -s -H "$AUTH" "$WEBUI_URL/api/v1/knowledge/" \
  | jq -r --arg n "$name" '(.items // .)[]? | select(.name == $n) | .id' | head -1)
if [ -n "$existing" ]; then
  echo "[seed-knowledge] collection '$name' already exists: id=$existing" >&2
  echo "  Delete it in WebUI first if you want to re-ingest." >&2
  exit 0
fi

# Create the collection.
echo "[seed-knowledge] creating collection: $name"
collection_id=$(curl -s -H "$AUTH" -H "$JSON" \
  -X POST "$WEBUI_URL/api/v1/knowledge/create" \
  -d "$(jq -n --arg name "$name" --arg desc "$desc" \
        '{name:$name, description:$desc, access_control:null}')" \
  | jq -r .id)
echo "[seed-knowledge] collection id: $collection_id"

# Upload and attach each source.
jq -c '.sources[]' "$MANIFEST" | while read -r src; do
  host_path=$(jq -r .host_path <<<"$src")
  filename=$(jq -r .filename <<<"$src")
  if [ ! -f "$host_path" ]; then
    echo "[seed-knowledge] WARN: skipping missing source: $host_path" >&2
    continue
  fi
  echo "[seed-knowledge] upload: $filename ($(stat -c%s "$host_path") bytes)"
  file_id=$(curl -s -H "$AUTH" -X POST "$WEBUI_URL/api/v1/files/" \
    -F "file=@$host_path;filename=$filename" \
    | jq -r .id)
  if [ -z "$file_id" ] || [ "$file_id" = "null" ]; then
    echo "[seed-knowledge] FATAL: file upload failed for $filename" >&2
    exit 1
  fi
  curl -s -H "$AUTH" -H "$JSON" \
    -X POST "$WEBUI_URL/api/v1/knowledge/$collection_id/file/add" \
    -d "$(jq -n --arg id "$file_id" '{file_id:$id}')" >/dev/null
  echo "[seed-knowledge]   file_id=$file_id added"
done

echo
echo "[seed-knowledge] DONE. Collection id: $collection_id"
echo
echo "Next: attach to llama-rag by setting meta.knowledge in seeds/models.json:"
cat <<JSON
  "knowledge": [
    {
      "id": "$collection_id",
      "name": "$name",
      "description": "$desc",
      "type": "collection"
    }
  ]
JSON
echo
echo "Then re-run: harbor down webui && harbor up webui"
