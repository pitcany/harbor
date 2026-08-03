# Open WebUI Tool-Use Architecture Map

_Snapshot: 2026-08-02. All keys/tokens redacted. Read-only inspection of the live system._

## Deployment

- **Open WebUI 0.9.6** (`ghcr.io/open-webui/open-webui:0.9.6`) as `harbor.webui`, published on host `:33801`, managed by the Harbor compose stack (`~/.harbor`).
- Custom entrypoint `services/webui/start_webui.sh`:
  1. installs sqlite3 CLI (debug convenience),
  2. merges `services/webui/configs/*.json` → `/app/backend/data/config.json` via `json_config_merger.py` (this JSON is imported into the **DB `config` table** at boot — the DB is the effective runtime config),
  3. seeds workspace model presets from `services/webui/seeds/models.json`,
  4. applies a `files.py` pydantic patch (from_attributes).
- Two source files are **bind-mounted over the image**:
  - `services/webui/chroma_patched.py` → `retrieval/vector/dbs/chroma.py`
  - `services/webui/retrieval_utils_patched.py` → `retrieval/utils.py` (uncommitted; imports cleanly in the container)
  Both are version-pinned to 0.9.6 — any image bump must re-derive these patches.
- Persistent data: `services/webui/webui.db` (SQLite, 2.3 GB, WAL), `uploads/`, `vector_db/` (legacy chroma), qdrant storage under `services/qdrant/storage/`. 1 admin user, 126 chats, 6 knowledge bases, 1 API key.

## Model providers (effective, from DB config)

`openai.api_base_urls` (7 entries):

| URL | What it is | State |
|---|---|---|
| `http://boost:8000/v1` | harbor.boost (Harbor's optimizing proxy) | **DOWN — python process crash-loops: `ModuleNotFoundError: shortuuid`** |
| `http://pipelines:9099` | Open WebUI Pipelines | up |
| `http://172.18.0.1:4000/v1` | **LiteLLM gateway** (host, systemd `litellm`) — the main front door | up |
| `http://172.17.0.1:8090/v1` | old math-RAG shim | **DEAD — nothing listens on 8090** |
| `http://172.17.0.1:8091/v1` | `rag-webui-shim` → `local-math-rag`, `calc-math-rag` | up |
| `http://172.17.0.1:8093/v1` | `rag-webui-shim` → `auto-math-rag` | up |
| `http://172.17.0.1:8094/v1` | `rag-webui-shim` → `applied-math-rag` | up |

- **Ollama** at `http://172.17.0.1:11434` (chat API + RAG embeddings; embedder `qwen3-embedding-4b-gpu` pinned on GPU, keep-alive forever). Ollama *cloud* models (`deepseek-v4-pro:cloud`, etc.) ride this connection.
- **LiteLLM gateway** (`~/AI/llm-router/litellm/config.yaml`) fans out to:
  - local: `qwen`, `qwen-local`, `llama`, `llama-fast`, `deepseek-r1-70b`, `coder` → **llm-router** `127.0.0.1:4100`
  - cloud: Anthropic (Claude 5 family, per-model `additional_drop_params`), OpenAI (gpt-5.x, o-series), OpenRouter (gemini/deepseek/grok/qwen3-coder/fusion), Venice.
- **llm-router** (`~/AI/llm-router/config/router.yaml`): virtual-model router that **swaps the single TP=2 `vllm-tp` systemd unit's profile on demand** (rewrites `vllm-tp.env`, `systemctl restart vllm-tp`, health-waits up to 300 s). Only one local vLLM model is resident at a time; selecting a cold model in OWUI implies a multi-minute swap. `AIOHTTP_CLIENT_TIMEOUT=900` in webui exists to survive this.
- **vllm-tp** currently serves `Qwen/Qwen3.6-27B-FP8` as `qwen3.6-27b`, with `--enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3` (native tool calling correctly enabled at the server).

## Workspace model profiles (DB `model` table)

| Profile id | Display | Base | function_calling | Tools attached |
|---|---|---|---|---|
| `local-repo-ops` | Repo Ops (local) | `qwen-local` (LiteLLM→router→vllm) | **native** | `server:mcp-github` |
| `cloud-tools` | Solver (cloud) | `deepseek-v4-pro:cloud` (Ollama cloud) | **native** | none by default (user picks per-chat) |
| `local-math-neighbor` | Math Tutor (offline) | `local-math-rag` (shim :8091) | `none`* | none |
| `auto-math-neighbor` | Math Tutor | `auto-math-rag` (shim :8093) | `none`* | none |
| `calc-math` | Calculator | `calc-math-rag` (shim :8091) | `none`* | none |
| `local-applied` | Code & Applied Stats | `applied-math-rag` (shim :8094) | `none`* | none |
| `cloud-proofs` | Math Tutor (cloud) | `cloud-proofs-rag` | `none`* | none (inactive) |

\* `"none"` is **not a value 0.9.6 understands** — middleware only tests `== "native"`, so `"none"` silently behaves as `"default"` (prompt-based compat mode). Harmless while no tools are attached, but misleading.

`ui.default_models` = `llama-applied` — **stale**: no such model id exists anywhere.

Task model (title/tags/autocomplete/tool-routing in default mode): `qwen-local`; title + follow-up generation disabled.

## Tool plane

Tool servers are OpenAPI connections in the DB config (17 entries), all backed by MCP servers behind two mcpo layers plus a hand-rolled read-only proxy:

- **`harbor.mcpo`** (`ghcr.io/av/tools`, container, `http://mcpo:8000/<name>`): fetch, time, sequential-thinking, memory, search (DDG), searxng, git (disabled), arxiv, duckdb — config in `services/mcpo/configs/mcpo.override.json`.
- **owui-mcpo** (`ghcr.io/open-webui/mcpo`, host-published `127.0.0.1:8200`) — a second, separate mcpo from the `openwebui-addons` stack.
- **Read-only proxy `172.18.0.1:8212`** (host python3, listen backlog 5): fs-ai, fs-ext, github, crawl4ai, playwright, hf — strips/refuses mutating operations.
- **Wolfram** `172.18.0.1:8210` (mcpo) and **wolfram-plot** `172.18.0.1:8211` (uvicorn), with systemd healthcheck timer.
- All 16 enabled endpoints answered `openapi.json` in <11 ms from inside the webui container (probe 2026-08-02).

Other integrations:

- **Web search**: engine `brave` (API key via env); searxng config retained as fallback. Result count 8, embedding/retrieval NOT bypassed (results go through RAG).
- **Web loader**: external → `owui-crawl4ai-adapter:8000/load` (crawl4ai).
- **RAG**: qdrant (`http://qdrant:6333`), ollama embedder `qwen3-embedding-4b-gpu`, hybrid search off, reranker configured (`BAAI/bge-reranker-v2-m3`), top_k 50 → rerank top 10, docling for content extraction.
- **Code execution + interpreter**: Jupyter at `owui-jupyter:8888` (token auth), timeout 60 s.
- **Functions**: one global filter `math_delimiter_normalizer`. No custom OWUI Python tools (`tool` table empty) — all tools are external OpenAPI servers.
- **Pipelines** (`pipelines:9099`) connected as a backend.

## Networking

- Docker bridge networks: harbor compose net (`172.18.0.x`, gateway `172.18.0.1` used to reach host services) and default bridge (`172.17.0.1` for ollama + shims).
- Tailscale exposure: WebUI reachable over tailnet at `:33801` (host binding `0.0.0.0`). No reverse proxy / TLS layer inside this stack.
- Timeouts: `AIOHTTP_CLIENT_TIMEOUT=900` (chat), Jupyter 60 s, playwright tool 10 s internal; mcpo/proxy layers have no explicit per-call timeout.
- Logging: `GLOBAL_LOG_LEVEL=DEBUG` — aiosqlite DEBUG noise dominates `docker logs harbor.webui`, burying real errors.

## Sibling stack (not Harbor-managed, but shares tools)

`owui-*` containers from `openwebui-addons`: crawl4ai + adapter, jupyter, second mcpo (:8200). LiteLLM spend DB (postgres :5433). harbor.ldr, harbor.speaches, harbor.docling, harbor.searxng, harbor.qdrant, harbor.pipelines, harbor.boost.

## Request path for a tool-using chat (native mode)

UI → OWUI backend (socket + `/api/chat/completions`) → middleware builds `tools` array from enabled tool servers (+ per-model `toolIds`) → LiteLLM :4000 → llm-router :4100 → vLLM :8003 (qwen3_xml parser emits `tool_calls` deltas) → OWUI executes tool via aiohttp against mcpo/proxy → result appended as `role:tool` message → model continuation → SSE stream to UI.

In **default mode** the task model (`qwen-local`) picks tools via a JSON prompt template instead, then the selected tools run once before the main completion.
