#!/usr/bin/env python3
"""Mock OpenAPI tool server for Open WebUI failure-mode testing.

Serves deliberately misbehaving endpoints so the harness can observe how the
full OWUI pipeline handles upstream 401/429/500, timeouts, oversized results,
and malformed JSON. Binds the docker bridge gateway so containers can reach it.

Usage: python3 mock_tool_server.py [--host 172.17.0.1] [--port 8097]
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "mock-fault", "description": "Fault-injection test tools", "version": "1.0"},
    "paths": {
        "/echo": {"post": {
            "operationId": "mock_echo",
            "description": "Echo back the provided text. Use when asked to 'mock echo' something.",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "properties": {"text": {"type": "string", "description": "text to echo"}},
                "required": ["text"]}}}},
            "responses": {"200": {"description": "ok"}},
        }},
        "/unauthorized": {"post": {
            "operationId": "mock_unauthorized",
            "description": "Always fails with 401. Use when asked to call the unauthorized mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
        "/ratelimited": {"post": {
            "operationId": "mock_ratelimited",
            "description": "Always fails with 429. Use when asked to call the ratelimited mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
        "/crash": {"post": {
            "operationId": "mock_crash",
            "description": "Always fails with 500. Use when asked to call the crash mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
        "/slow": {"post": {
            "operationId": "mock_slow",
            "description": "Takes a very long time. Use when asked to call the slow mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
        "/huge": {"post": {
            "operationId": "mock_huge",
            "description": "Returns a very large payload. Use when asked to call the huge mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
        "/malformed": {"post": {
            "operationId": "mock_malformed",
            "description": "Returns broken JSON. Use when asked to call the malformed mock tool.",
            "responses": {"200": {"description": "ok"}},
        }},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        print(f"[mock] {self.command} {self.path} {args[1] if len(args) > 1 else ''}", flush=True)

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/openapi.json":
            return self._send(200, SPEC)
        return self._send(404, {"detail": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        route = self.path.rstrip("/")
        if route == "/echo":
            return self._send(200, {"echoed": payload.get("text", "")})
        if route == "/unauthorized":
            return self._send(401, {"detail": "Invalid API key"})
        if route == "/ratelimited":
            return self._send(429, {"detail": "Rate limit exceeded, retry later"})
        if route == "/crash":
            return self._send(500, {"detail": "Internal server error (simulated)"})
        if route == "/slow":
            time.sleep(int(payload.get("seconds", 300)))
            return self._send(200, {"ok": True})
        if route == "/huge":
            return self._send(200, {"data": "lorem-ipsum " * 40000})  # ~480 KB
        if route == "/malformed":
            return self._send(200, b'{"broken": [1, 2', )
        return self._send(404, {"detail": "not found"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="172.17.0.1")
    ap.add_argument("--port", type=int, default=8097)
    a = ap.parse_args()
    print(f"mock tool server on {a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
