#!/usr/bin/env bash
# Read-only diagnostic for the Open WebUI tool-use stack.
# Produces a redacted report on stdout. Changes nothing.
# Exit codes: 0 = healthy, 1 = warnings only, 2 = at least one failure.

set -u
WARN=0; FAIL=0
ok()   { printf ' [ OK ] %s\n' "$1"; }
warn() { printf ' [WARN] %s\n' "$1"; WARN=1; }
bad()  { printf ' [FAIL] %s\n' "$1"; FAIL=1; }

echo "== Open WebUI tool-use diagnostics ($(date -Is)) =="

echo "-- Containers --"
for c in harbor.webui harbor.mcpo harbor.qdrant harbor.searxng harbor.docling \
         harbor.boost harbor.pipelines owui-jupyter owui-crawl4ai-adapter; do
  state=$(docker inspect "$c" --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null)
  case "$state" in
    "running healthy"|"running ") ok "$c: $state" ;;
    running*) warn "$c: $state" ;;
    "") bad "$c: not found" ;;
    *) bad "$c: $state" ;;
  esac
done

echo "-- Open WebUI --"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:33801/health)
[ "$code" = 200 ] && ok "webui /health 200" || bad "webui /health -> $code"

echo "-- Model backends (as configured in the webui DB) --"
# This probe carries each backend's configured key, so a 401 is a failure: a
# gateway that rejects our key is as unusable as one that is unreachable. That
# is exactly how a dead LiteLLM spend DB presents — liveness keeps answering
# while every key lookup 401s and the models silently vanish.
backends=$(docker exec -i harbor.webui python3 - <<'PYEOF'
import json, sqlite3, urllib.request
con = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
cfg = json.loads(con.execute('select data from config order by id desc limit 1').fetchone()[0])
urls = cfg['openai']['api_base_urls']
keys = cfg['openai'].get('api_keys', [])
for i, u in enumerate(urls):
    key = keys[i] if i < len(keys) else ''
    req = urllib.request.Request(u.rstrip('/') + '/models',
                                 headers={'Authorization': f'Bearer {key}'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            n = len(json.load(r).get('data', []))
        print(f' [ OK ] backend {u} -> {n} models')
    except Exception as e:
        print(f' [FAIL] backend {u} -> {type(e).__name__}: {str(e)[:80]}')
ollama = cfg.get('rag', {}).get('ollama', {}).get('url')
print(f' (rag embedder endpoint: {ollama})')
PYEOF
)
printf '%s\n' "$backends"
if printf '%s\n' "$backends" | grep -q '\[FAIL\]'; then FAIL=1; fi

echo "-- Tool servers (from inside the webui container) --"
tools=$(docker exec -i harbor.webui python3 - <<'PYEOF'
import json, sqlite3, time, urllib.request
con = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
cfg = json.loads(con.execute('select data from config order by id desc limit 1').fetchone()[0])
for conn in cfg.get('tool_server', {}).get('connections', []):
    if not conn.get('config', {}).get('enable', False):
        continue
    name = conn.get('info', {}).get('id') or conn['url']
    url = conn['url'].rstrip('/') + '/' + conn.get('path', 'openapi.json')
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            spec = json.load(r)
        ops = sum(len(v) for v in spec.get('paths', {}).values())
        print(f' [ OK ] tool {name}: {ops} ops, {int((time.monotonic()-t0)*1000)} ms')
    except Exception as e:
        print(f' [FAIL] tool {name}: {type(e).__name__}: {str(e)[:80]}')
PYEOF
)
printf '%s\n' "$tools"
if printf '%s\n' "$tools" | grep -q '\[FAIL\]'; then FAIL=1; fi

echo "-- Local inference --"
systemctl is-active --quiet ollama && ok "ollama active" || warn "ollama inactive"
if systemctl is-active --quiet vllm-tp; then
  m=$(curl -s -m 5 http://127.0.0.1:8003/v1/models | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
  ok "vllm-tp active (serving: ${m:-unknown})"
else
  warn "vllm-tp inactive (local vLLM models unavailable until started)"
fi
emb=$(ollama ps 2>/dev/null | grep -c embedding)
[ "${emb:-0}" -ge 1 ] && ok "embedding model resident in ollama" || warn "no embedding model resident (first RAG query will load it)"

echo "-- Config sanity --"
cfgcheck=$(docker exec -i harbor.webui python3 - <<'PYEOF'
import json, sqlite3
con = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
cfg = json.loads(con.execute('select data from config order by id desc limit 1').fetchone()[0])
default = cfg.get('ui', {}).get('default_models')
ids = [r[0] for r in con.execute('select id from model where is_active=1')]
if default and default not in ids:
    print(f' [WARN] ui.default_models={default!r} does not match any active workspace model {ids}')
else:
    print(f' [ OK ] ui.default_models={default!r}')
task = cfg.get('task', {}).get('model', {})
print(f" ( task model: default={task.get('default')} external={task.get('external')} )")
PYEOF
)
printf '%s\n' "$cfgcheck"
if printf '%s\n' "$cfgcheck" | grep -q '\[WARN\]'; then WARN=1; fi

echo "-- Recent errors (webui, last 6h, deduplicated) --"
docker logs harbor.webui --since 6h 2>&1 \
  | grep '| ERROR ' | grep -v aiosqlite \
  | sed -E 's/^[0-9-]+ [0-9:.]+ \| //; s/[A-Za-z0-9_-]{20,}/<REDACTED>/g' \
  | sort | uniq -c | sort -rn | head -8
echo "(end of error digest)"

echo "== Result: $( [ $FAIL = 1 ] && echo FAIL || { [ $WARN = 1 ] && echo WARN || echo HEALTHY; } ) =="
[ $FAIL = 1 ] && exit 2
[ $WARN = 1 ] && exit 1
exit 0
