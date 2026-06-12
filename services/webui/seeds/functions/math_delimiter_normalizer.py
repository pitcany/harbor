"""
title: Math Delimiter Normalizer
author: yannik
version: 0.1.0
required_open_webui_version: 0.5.0
description: Repairs/normalizes LaTeX math delimiters in model output so KaTeX renders reliably (\\[ \\] -> $$, \\( \\) -> $, balances stray $$). Code/inline-code spans are left untouched.
"""
import re
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        normalize_bracket_delims: bool = Field(
            default=True, description=r"Convert \[ \] -> $$ display and \( \) -> $ inline"
        )
        balance_double_dollar: bool = Field(
            default=True, description="Append a closing $$ when the $$ count is odd"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _normalize(self, text: str) -> str:
        if not text or "\x00" in text:
            return text
        # Protect fenced + inline code so we never rewrite math-looking code
        stash = []
        def _hide(m):
            stash.append(m.group(0))
            return f"\x00{len(stash)-1}\x00"
        text = re.sub(r"```.*?```", _hide, text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", _hide, text)

        if self.valves.normalize_bracket_delims:
            text = re.sub(r"\\\[\s*(.+?)\s*\\\]",
                          lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n", text, flags=re.S)
            text = re.sub(r"\\\(\s*(.+?)\s*\\\)",
                          lambda m: f"${m.group(1).strip()}$", text, flags=re.S)

        if self.valves.balance_double_dollar and text.count("$$") % 2 == 1:
            text = text.rstrip() + "\n$$"

        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        try:
            for msg in body.get("messages", []):
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                    msg["content"] = self._normalize(msg["content"])
        except Exception:
            pass
        return body
