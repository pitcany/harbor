#!/usr/bin/env python3
"""Diagnostic harness for Open WebUI tool use.

Drives POST /api/chat/completions (the same middleware path the UI uses,
including tool-server resolution and native/default function calling) and
records a per-request timeline of SSE events with timestamps.

Failure classes distinguished (see classify()):
  no_tool_requested, wrong_tool, invalid_args, tool_error, no_final_answer,
  http_error, timeout, stream_stall, unexpected_tool_use, empty_answer, ok

Usage:
  python3 owui_tool_harness.py --scenarios scenarios_baseline.json \
      [--only ID ...] [--repeat N] [--out results.json] [--jsonl log.jsonl]

The OWUI API key is taken from $OWUI_API_KEY, or read from the webui DB via
`docker exec` (single admin install). Secrets are never written to logs.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid

OWUI_URL = os.environ.get("OWUI_URL", "http://127.0.0.1:33801")
DEFAULT_TIMEOUT = 240  # generous: local 27B + tools
STALL_LIMIT = 150      # max seconds with no bytes on the stream


def get_api_key():
    key = os.environ.get("OWUI_API_KEY")
    if key:
        return key
    code = (
        "import sqlite3;"
        "con=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);"
        "print(con.execute('select key from api_key limit 1').fetchone()[0])"
    )
    out = subprocess.run(
        ["docker", "exec", "harbor.webui", "python3", "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    key = out.stdout.strip()
    if not key:
        sys.exit("No OWUI API key found (set OWUI_API_KEY or create one in the UI)")
    return key


def sse_events(resp, deadline, timeline):
    """Yield parsed SSE data payloads, tracking byte timestamps."""
    buf = b""
    first = True
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timeline.append((time.time(), "timeout", None))
            return
        try:
            chunk = resp.read1(65536)
        except Exception as e:
            timeline.append((time.time(), "read_error", str(e)[:200]))
            return
        if not chunk:
            return
        if first:
            timeline.append((time.time(), "first_byte", None))
            first = False
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            for line in raw.split(b"\n"):
                if line.startswith(b"data: "):
                    payload = line[6:].strip()
                    if payload == b"[DONE]":
                        timeline.append((time.time(), "done", None))
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        timeline.append((time.time(), "bad_json_chunk", payload[:120].decode("utf-8", "replace")))


DETAILS_RE = re.compile(
    r'<details type="tool_calls" done="(?P<done>\w+)" id="[^"]*" name="(?P<name>[^"]*)" arguments="(?P<args>[^"]*)"'
)
LOG_TOOLCALL_RE = re.compile(r"tool_call_handler:\d+ - tool_call=(\{.*)")
LOG_CTX_RE = re.compile(r"tool_contexts: \[")


def collect_server_evidence(since_iso):
    """Scrape harbor.webui logs since a timestamp for tool execution markers."""
    try:
        out = subprocess.run(
            ["docker", "logs", "harbor.webui", "--since", since_iso],
            capture_output=True, text=True, timeout=30,
        )
        lines = (out.stdout or "") + (out.stderr or "")
    except Exception:
        return {"tool_calls": [], "tool_contexts": 0, "errors": []}
    calls, errors, ctx = [], [], 0
    for line in lines.splitlines():
        m = LOG_TOOLCALL_RE.search(line)
        if m:
            try:
                calls.append(ast.literal_eval(m.group(1)))  # log prints a python dict literal
            except Exception:
                calls.append({"name": m.group(1)[:120], "parameters": None})
        if LOG_CTX_RE.search(line):
            ctx += 1
        if "| ERROR " in line and "aiosqlite" not in line:
            errors.append(line.split("| ERROR", 1)[-1][:200])
    return {"tool_calls": calls, "tool_contexts": ctx, "errors": errors}


def api_json(api_key, method, path, body=None, timeout=30):
    req = urllib.request.Request(
        f"{OWUI_URL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


UI_DETAILS_RE = re.compile(
    r'<details type="tool_calls" done="(?P<done>\w+)" id="[^"]*" name="(?P<name>[^"]*)" '
    r'arguments="(?P<args>[^"]*)"[^>]*>.*?<summary>[^<]*</summary>\n?(?P<result>.*?)</details>',
    re.S,
)


def run_ui_mode(scenario, api_key, timeout):
    """Drive the real UI flow: create a chat, run completion with session ids so
    the server-side native tool loop executes, poll the persisted message."""
    import html as _html
    corr = str(uuid.uuid4())[:8]
    u_id, a_id, sess = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    now = int(time.time())
    prompt = scenario["prompt"]
    model = scenario["model"]
    result = {
        "corr_id": corr, "scenario": scenario["id"], "model": model,
        "tool_ids": scenario.get("tool_ids") or [], "tool_calls": [], "tool_results": [],
        "content_chars": 0, "content": "", "first_token_ts": None, "status_events": [],
        "sources": 0, "usage": None, "error": None, "http_status": None, "mode": "ui",
    }
    timeline = [(time.time(), "request_sent", None)]
    chat_id = None
    try:
        chat = {
            "title": f"harness-{scenario['id']}",
            "models": [model],
            "history": {"currentId": a_id, "messages": {
                u_id: {"id": u_id, "parentId": None, "childrenIds": [a_id], "role": "user",
                       "content": prompt, "timestamp": now, "models": [model]},
                a_id: {"id": a_id, "parentId": u_id, "childrenIds": [], "role": "assistant",
                       "content": "", "model": model, "timestamp": now}}},
            "messages": [], "tags": [], "timestamp": now * 1000,
        }
        chat_id = api_json(api_key, "POST", "/api/v1/chats/new", {"chat": chat})["id"]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True, "chat_id": chat_id, "id": a_id,
            "session_id": sess, "parent_id": u_id,
        }
        if scenario.get("tool_ids"):
            body["tool_ids"] = scenario["tool_ids"]
        if scenario.get("params"):
            body.update(scenario["params"])
        resp = api_json(api_key, "POST", "/api/chat/completions", body, timeout=60)
        result["http_status"] = 200
        timeline.append((time.time(), "task_accepted", ",".join(resp.get("task_ids", []))))
        deadline = time.monotonic() + timeout
        content, done = "", False
        stop_after = scenario.get("stop_after_s")
        stopped = False
        while time.monotonic() < deadline:
            time.sleep(2.5)
            if stop_after and not stopped and time.time() - timeline[0][0] > stop_after:
                for tid in resp.get("task_ids", []):
                    try:
                        api_json(api_key, "POST", f"/api/tasks/stop/{tid}")
                    except Exception as e:
                        result["error"] = f"stop failed: {e}"
                stopped = True
                timeline.append((time.time(), "stop_requested", None))
            c = api_json(api_key, "GET", f"/api/v1/chats/{chat_id}")
            msg = c["chat"]["history"]["messages"].get(a_id, {})
            new_content = msg.get("content", "") or ""
            if new_content and not content:
                timeline.append((time.time(), "first_content_persisted", None))
            content = new_content
            if msg.get("error"):
                result["error"] = str(msg.get("error"))[:300]
            if msg.get("done"):
                done = True
                timeline.append((time.time(), "done", None))
                break
        if not done:
            timeline.append((time.time(), "timeout", None))
        result["content"] = content[:8000]
        result["content_chars"] = len(content)
        for m in UI_DETAILS_RE.finditer(content):
            args = _html.unescape(m.group("args"))
            try:
                parsed = json.loads(args)
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                args_ok = True
                args_repr = json.dumps(parsed)[:500]
            except (json.JSONDecodeError, TypeError):
                args_ok, args_repr = False, args[:500]
            res_text = _html.unescape(m.group("result")).strip()
            result["tool_calls"].append(
                {"name": m.group("name"), "arguments": args_repr,
                 "args_valid_json": args_ok, "ts": None, "via": "ui_details"})
            result["tool_results"].append(
                {"name": m.group("name"), "ok": not res_text.strip('"').startswith("Error"),
                 "chars": len(res_text), "excerpt": res_text[:200]})
        # final answer = text after the last details block
        tail = re.split(r"</details>\n?", content)[-1]
        result["final_answer_chars"] = len(tail.strip())
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        if chat_id and not scenario.get("keep_chat"):
            try:
                api_json(api_key, "DELETE", f"/api/v1/chats/{chat_id}")
            except Exception:
                pass
    t0 = timeline[0][0]
    ev = collect_server_evidence(time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0 - 1)))
    result["server_errors"] = ev["errors"]
    result["server_tool_contexts"] = ev["tool_contexts"]
    for c in ev["tool_calls"]:
        if not c.get("name"):
            continue  # empty selection: model answered {"tool_calls": []}
        result["tool_calls"].append(
            {"name": str(c.get("name", "?")), "arguments": json.dumps(c.get("parameters"))[:500],
             "args_valid_json": isinstance(c.get("parameters"), dict), "ts": None, "via": "server_log"})
    result["timeline"] = [{"ts": ts, "event": n, "detail": d} for ts, n, d in timeline]
    result["total_s"] = round(timeline[-1][0] - t0, 2)
    return result


def run_one(scenario, api_key, timeout):
    if scenario.get("ui_mode"):
        return run_ui_mode(scenario, api_key, timeout)
    corr = str(uuid.uuid4())[:8]
    messages = scenario.get("messages") or [{"role": "user", "content": scenario["prompt"]}]
    body = {
        "model": scenario["model"],
        "messages": messages,
        "stream": True,
    }
    if scenario.get("tool_ids"):
        body["tool_ids"] = scenario["tool_ids"]
    if scenario.get("params"):
        body.update(scenario["params"])

    timeline = [(time.time(), "request_sent", None)]
    result = {
        "corr_id": corr,
        "scenario": scenario["id"],
        "model": scenario["model"],
        "tool_ids": scenario.get("tool_ids") or [],
        "tool_calls": [],       # [{name, arguments, ts}]
        "tool_results": [],     # [{name, ok, ts, chars}]
        "content_chars": 0,
        "content": "",
        "first_token_ts": None,
        "status_events": [],
        "sources": 0,
        "usage": None,
        "error": None,
        "http_status": None,
    }

    req = urllib.request.Request(
        f"{OWUI_URL}/api/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    deadline = time.monotonic() + timeout
    cancel_after = scenario.get("cancel_after_s")
    partial_tool = {}
    try:
        resp = urllib.request.urlopen(req, timeout=min(timeout, STALL_LIMIT))
        result["http_status"] = resp.status
        for ev in sse_events(resp, deadline, timeline):
            if cancel_after and time.time() - timeline[0][0] > cancel_after:
                timeline.append((time.time(), "client_cancelled", None))
                resp.close()
                break
            now = time.time()
            if "event" in ev and isinstance(ev.get("event"), dict):
                etype = ev["event"].get("type", "?")
                edata = ev["event"].get("data")
                summary = None
                if isinstance(edata, dict):
                    summary = str(edata.get("description") or edata.get("error") or "")[:160]
                result["status_events"].append({"ts": now, "type": etype, "summary": summary})
                timeline.append((now, f"event:{etype}", summary))
                if etype == "chat:message:error":
                    result["error"] = summary
                continue
            for choice in ev.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    if result["first_token_ts"] is None:
                        result["first_token_ts"] = now
                        timeline.append((now, "first_content_token", None))
                    result["content_chars"] += len(delta["content"])
                    if len(result["content"]) < 4000:
                        result["content"] += delta["content"]
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = partial_tool.setdefault(idx, {"name": "", "arguments": "", "ts": now})
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                        timeline.append((now, "tool_call_emitted", fn["name"]))
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    timeline.append((now, f"finish:{choice['finish_reason']}", None))
            if ev.get("sources"):
                result["sources"] += len(ev["sources"])
            if ev.get("usage"):
                result["usage"] = {k: ev["usage"].get(k) for k in ("prompt_tokens", "completion_tokens")}
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["error"] = f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    # finalize accumulated tool calls
    for slot in partial_tool.values():
        args_ok = True
        try:
            json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            args_ok = False
        result["tool_calls"].append(
            {"name": slot["name"], "arguments": slot["arguments"][:500], "args_valid_json": args_ok, "ts": slot["ts"]}
        )

    # native-mode tool calls arrive serialized inside the content stream
    for m in DETAILS_RE.finditer(result["content"]):
        import html as _html
        args = _html.unescape(m.group("args"))
        args_ok = True
        if args:
            try:
                json.loads(args)
            except json.JSONDecodeError:
                args_ok = False
        result["tool_calls"].append(
            {"name": m.group("name"), "arguments": args[:500],
             "args_valid_json": args_ok, "ts": None, "via": "native_details"}
        )

    if cancel_after:
        # after client cancel, watch whether upstream generation actually stops
        time.sleep(8)
        try:
            metrics = urllib.request.urlopen("http://127.0.0.1:8003/metrics", timeout=5).read().decode()
            running = [l for l in metrics.splitlines() if l.startswith("vllm:num_requests_running")]
            result["vllm_running_after_cancel"] = running[0].split()[-1] if running else None
        except Exception:
            result["vllm_running_after_cancel"] = "unavailable"

    t0 = timeline[0][0]
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0 - 1))
    ev = collect_server_evidence(since_iso)
    for c in ev["tool_calls"]:
        if not c.get("name"):
            continue  # empty selection: model answered {"tool_calls": []}
        result["tool_calls"].append(
            {"name": str(c.get("name", "?")), "arguments": json.dumps(c.get("parameters"))[:500],
             "args_valid_json": isinstance(c.get("parameters"), dict), "ts": None, "via": "server_log"}
        )
    result["server_tool_contexts"] = ev["tool_contexts"]
    result["server_errors"] = ev["errors"]

    result["timeline"] = [
        {"ts": ts, "event": name, "detail": detail} for ts, name, detail in timeline
    ]
    result["total_s"] = round((timeline[-1][0] - t0), 2)
    return result


def classify(scenario, result):
    """Map one run to (passed, failure_class)."""
    exp = scenario.get("expect", {})
    called = [t["name"] for t in result["tool_calls"]]
    ev_types = [e["type"] for e in result["status_events"]]

    if scenario.get("cancel_after_s"):
        cancelled = any(e["event"] == "client_cancelled" for e in result["timeline"])
        return (True, "ok_cancelled") if cancelled else (False, "cancel_not_reached")
    if result["error"] and result["http_status"] not in (200, None):
        return False, "http_error"
    if any(e["event"] == "timeout" for e in result["timeline"]):
        return False, "timeout"
    if result["error"] and not result["content_chars"]:
        # tolerated when scenario expects a surfaced error
        if exp.get("surfaced_error"):
            return True, "ok_surfaced_error"
        return False, "tool_error" if called else "request_error"

    want_tool = exp.get("tool")           # substring that must appear in a called tool name
    forbid_tools = exp.get("no_tools", False)

    if forbid_tools and called:
        return False, "unexpected_tool_use"
    if want_tool:
        if not called and not any("tool" in t for t in ev_types):
            return False, "no_tool_requested"
        if called and not any(want_tool in c for c in called):
            return False, "wrong_tool"
        if any(not t["args_valid_json"] for t in result["tool_calls"]):
            return False, "invalid_args"
    # a tool that ran but returned an error result
    failed_tools = [t for t in result.get("tool_results", []) if not t.get("ok", True)]
    if failed_tools and not exp.get("surfaced_error"):
        answer_len = result.get("final_answer_chars", result["content_chars"])
        if answer_len > 0:
            return (True, "ok_error_surfaced") if exp.get("tolerate_tool_error") else (False, "tool_error_surfaced")
        return False, "tool_error_no_answer"
    if exp.get("answer", True):
        answer_len = result.get("final_answer_chars", result["content_chars"])
        if answer_len == 0:
            return False, "no_final_answer"
        if exp.get("answer_regex"):  # checked against captured content
            # caller records content externally? we only have counts; regex is
            # checked against captured content when capture_content is set
            content = result.get("content", "")
            if content and not re.search(exp["answer_regex"], content, re.I | re.S):
                return False, "answer_mismatch"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--repeat", type=int, default=None, help="override per-scenario repeat")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--out", default=None)
    ap.add_argument("--jsonl", default=None)
    args = ap.parse_args()

    with open(args.scenarios) as f:
        scenarios = json.load(f)
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]
    api_key = get_api_key()

    jsonl = open(args.jsonl, "a") if args.jsonl else None
    all_results = []
    for sc in scenarios:
        repeats = args.repeat or sc.get("repeat", 1)
        for i in range(repeats):
            t0 = time.strftime("%H:%M:%S")
            r = run_one(sc, api_key, sc.get("timeout", args.timeout))
            passed, cls = classify(sc, r)
            r["passed"], r["class"], r["trial"] = passed, cls, i + 1
            all_results.append(r)
            line = (
                f"[{t0}] {sc['id']} trial {i+1}/{repeats} model={sc['model']} "
                f"-> {'PASS' if passed else 'FAIL'} ({cls}) "
                f"tools={[t['name'] for t in r['tool_calls']]} "
                f"chars={r['content_chars']} t={r['total_s']}s"
            )
            print(line, flush=True)
            if jsonl:
                slim = {k: v for k, v in r.items() if k != "timeline"}
                jsonl.write(json.dumps({"corr_id": r["corr_id"], **slim}) + "\n")
                jsonl.flush()
    if jsonl:
        jsonl.close()

    summary = {}
    for r in all_results:
        s = summary.setdefault(r["scenario"], {"pass": 0, "fail": 0, "classes": {}, "latencies": []})
        s["pass" if r["passed"] else "fail"] += 1
        s["classes"][r["class"]] = s["classes"].get(r["class"], 0) + 1
        s["latencies"].append(r["total_s"])
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": summary, "results": all_results}, f, indent=1)
    print("\n=== SUMMARY ===")
    for sid, s in summary.items():
        lat = sorted(s["latencies"])
        print(f"{sid}: {s['pass']} pass / {s['fail']} fail  classes={s['classes']} "
              f"median={lat[len(lat)//2]}s")
    failed = sum(s["fail"] for s in summary.values())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
