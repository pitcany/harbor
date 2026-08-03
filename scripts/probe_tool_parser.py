#!/usr/bin/env python3
"""Probe whether the vLLM tool-call parser matches the served model.

A mismatched parser fails quietly: the model emits a perfectly good tool call,
the parser fails to recognise the format, and the call arrives as plain TEXT in
message.content instead of structured message.tool_calls. Open WebUI then shows
the model describing a tool call rather than making one.

Usage: python3 probe_tool_parser.py [--base http://127.0.0.1:8003] [--trials 3]
Exit 0 if the parser produces structured tool_calls, 1 otherwise.
"""

import argparse
import json
import re
import sys
import urllib.request

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Get the current time in a specific IANA timezone.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA timezone, e.g. Asia/Tokyo"}},
            "required": ["timezone"],
        },
    },
}]

# Signatures of a tool call that leaked into content as raw text.
LEAK = re.compile(r"<tool_call>|</tool_call>|<function|get_current_time|```json", re.I)


def probe(base, model, trial):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "What time is it in Tokyo right now? Use the tool."}],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 256,
    }
    req = urllib.request.Request(f"{base}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        msg = json.load(r)["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    content = (msg.get("content") or "").strip()
    if calls:
        fn = calls[0].get("function", {})
        args = fn.get("arguments", "")
        try:
            parsed = json.loads(args) if isinstance(args, str) else args
            ok_args = isinstance(parsed, dict) and "timezone" in parsed
        except json.JSONDecodeError:
            ok_args = False
        print(f"  trial {trial}: PARSED  name={fn.get('name')} args={args[:60]} args_valid={ok_args}")
        return True
    leaked = bool(LEAK.search(content))
    print(f"  trial {trial}: NO tool_calls{' — call LEAKED INTO TEXT' if leaked else ''}")
    if content:
        print(f"      content[:160]: {content[:160]!r}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8003")
    ap.add_argument("--trials", type=int, default=3)
    a = ap.parse_args()
    model = json.load(urllib.request.urlopen(f"{a.base}/v1/models", timeout=15))["data"][0]["id"]
    print(f"model: {model}")
    ok = sum(probe(a.base, model, i + 1) for i in range(a.trials))
    print(f"result: {ok}/{a.trials} trials produced structured tool_calls")
    sys.exit(0 if ok == a.trials else 1)


if __name__ == "__main__":
    main()
