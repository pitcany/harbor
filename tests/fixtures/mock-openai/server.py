import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", 8080))
# When set, /v1/* endpoints require Authorization: Bearer <REQUIRED_TOKEN>.
# Empty/unset disables auth (default — keeps the smoke battery simple).
REQUIRED_TOKEN = os.environ.get("MOCK_OPENAI_REQUIRED_TOKEN", "")

MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "mock-model", "object": "model", "created": 1677610602, "owned_by": "mock"}
    ],
}

CHAT_RESPONSE = {
    "id": "mock-chatcmpl-001",
    "object": "chat.completion",
    "created": 1677610602,
    "model": "mock-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello from mock-openai!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
}

KNOWN_MODELS = {m["id"] for m in MODELS_RESPONSE["data"]}


def _err(code: str, message: str, type_: str = "invalid_request_error") -> dict:
    # OpenAI-shaped error envelope. Matching this shape catches proxies that
    # un-nest `error` or rewrite the field names.
    return {"error": {"code": code, "message": message, "type": type_}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"{self.command} {self.path} - {format % args}", flush=True)

    def _maybe_sleep(self, query: dict) -> None:
        # ?latency_ms=N delays the response by N ms (capped at 5s) — lets
        # header-propagation tests assert mock respects a knob without
        # depending on the test runner's wall-clock noise.
        try:
            ms = int(query.get("latency_ms", ["0"])[0])
        except (TypeError, ValueError):
            ms = 0
        if ms > 0:
            time.sleep(min(ms, 5000) / 1000.0)

    def _common_headers(self) -> None:
        # Round-trip X-Request-Id when the caller sets one. A proxy that
        # rewrites or drops the header fails this assertion immediately.
        rid = self.headers.get("X-Request-Id")
        if rid:
            self.send_header("X-Request-Id", rid)

    def send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        if not REQUIRED_TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {REQUIRED_TOKEN}"

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n > 0 else b""

    def _send_sse_chat(self, model: str) -> None:
        # Minimal OpenAI-compatible SSE stream: a role delta, a few content
        # deltas, a stop frame, and the [DONE] sentinel. Every frame is its
        # own `data: …\n\n` block — the integration test asserts the final
        # bytes contain `[DONE]` and at least one delta carried content.
        # Connection: close so clients (curl, httpyac) terminate after [DONE]
        # rather than waiting for a content-length that never comes.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._common_headers()
        self.end_headers()
        # Override BaseHTTPRequestHandler's keep-alive disposition so the
        # outer request loop closes the socket after this handler returns.
        self.close_connection = True

        def frame(payload: dict) -> None:
            self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
            self.wfile.flush()

        base = {"id": "mock-chatcmpl-stream-001", "object": "chat.completion.chunk",
                "created": 1677610602, "model": model}
        frame({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        for piece in ("Hello", " from", " mock-openai!"):
            frame({**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
        frame({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self._maybe_sleep(query)
        if parsed.path == "/health":
            self.send_json(200, {"status": "ok", "service": "mock-openai"})
        elif parsed.path == "/v1/models":
            if not self._check_auth():
                self.send_json(401, _err("invalid_api_key", "Missing or invalid Authorization header", "authentication_error"))
                return
            self.send_json(200, MODELS_RESPONSE)
        else:
            self.send_json(404, _err("not_found", f"Unknown route: {parsed.path}"))

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self._maybe_sleep(query)
        body_raw = self._read_body()

        if parsed.path == "/v1/debug/echo":
            # Returns request body, headers, method, path verbatim. Catches
            # body-munging proxies (content-type rewrites, gzip mismatches,
            # JSON re-serialisation that drops fields).
            try:
                parsed_body = json.loads(body_raw) if body_raw else None
            except json.JSONDecodeError:
                parsed_body = None
            self.send_json(200, {
                "method": self.command,
                "path": parsed.path,
                "headers": {k: v for k, v in self.headers.items()},
                "raw_body": body_raw.decode("utf-8", errors="replace"),
                "json_body": parsed_body,
            })
            return

        if parsed.path == "/v1/chat/completions":
            if not self._check_auth():
                self.send_json(401, _err("invalid_api_key", "Missing or invalid Authorization header", "authentication_error"))
                return
            try:
                payload = json.loads(body_raw) if body_raw else {}
            except json.JSONDecodeError:
                self.send_json(400, _err("invalid_json", "Request body is not valid JSON"))
                return
            if not isinstance(payload.get("messages"), list) or not payload["messages"]:
                self.send_json(400, _err("missing_messages", "`messages` is required and must be a non-empty array"))
                return
            model = payload.get("model", "mock-model")
            if model not in KNOWN_MODELS:
                self.send_json(404, _err("model_not_found", f"Unknown model: {model}"))
                return
            if payload.get("stream"):
                self._send_sse_chat(model)
                return
            self.send_json(200, {**CHAT_RESPONSE, "model": model})
            return

        self.send_json(404, _err("not_found", f"Unknown route: {parsed.path}"))


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"mock-openai listening on port {PORT} (auth={'on' if REQUIRED_TOKEN else 'off'})", flush=True)
    server.serve_forever()
