r"""
title: Math Delimiter Normalizer
author: yannik
version: 0.2.0
required_open_webui_version: 0.5.0
description: Repairs LaTeX/Markdown math so KaTeX renders reliably. Folds orphaned reasoning (closing </think> with no opening tag, as Qwen3.5+ emits) back into a collapsible block, repairs mis-escaped currency (\\$ -> \$), converts \[ \] -> $$ and \( \) -> $, and balances stray $$ in the answer. Code/inline-code spans are left untouched.
"""
import re
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        fold_orphan_reasoning: bool = Field(
            default=True,
            description="Re-add a leading <think> when output has </think> but no opening tag (Qwen3.5+ template style), so Open WebUI folds the chain-of-thought instead of rendering it as prose.",
        )
        normalize_bracket_delims: bool = Field(
            default=True, description=r"Convert \[ \] -> $$ display and \( \) -> $ inline"
        )
        repair_currency: bool = Field(
            default=True, description=r"Collapse mis-escaped currency \\$<digit> -> \$<digit> so it doesn't open math",
        )
        balance_double_dollar: bool = Field(
            default=True, description="Append a closing $$ when the answer's $$ count is odd"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _fix_currency(self, text: str) -> str:
        # Two-or-more backslashes before a currency dollar is always a mis-escape:
        # `\\$10` renders as a literal backslash followed by a math-opening `$`.
        # Collapse to a single escaped dollar. Anchored to a digit so real math
        # (e.g. a trailing `\\` line break before `$$`) is never touched.
        if not self.valves.repair_currency:
            return text
        return re.sub(r"\\{2,}\$(?=\d)", r"\\$", text)

    def _fix_math(self, text: str) -> str:
        # Protect fenced + inline code so we never rewrite math-looking code.
        stash = []

        def _hide(m):
            stash.append(m.group(0))
            return f"\x00{len(stash) - 1}\x00"

        text = re.sub(r"```.*?```", _hide, text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", _hide, text)

        if self.valves.normalize_bracket_delims:
            text = re.sub(
                r"\\\[\s*(.+?)\s*\\\]",
                lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n",
                text,
                flags=re.S,
            )
            text = re.sub(
                r"\\\(\s*(.+?)\s*\\\)",
                lambda m: f"${m.group(1).strip()}$",
                text,
                flags=re.S,
            )

        text = self._fix_currency(text)

        if self.valves.balance_double_dollar and text.count("$$") % 2 == 1:
            text = text.rstrip() + "\n$$"

        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    def _normalize(self, text: str) -> str:
        if not text or "\x00" in text:
            return text

        # Fold leaked reasoning: Qwen3.5+ templates place the opening <think> in
        # the PROMPT, so generated output carries only a closing </think>. Open
        # WebUI can't fold a half-open reasoning block, so the chain-of-thought
        # (full of stray $/$$) renders as prose and breaks KaTeX. Re-add the
        # opening tag so the frontend collapses it.
        if (
            self.valves.fold_orphan_reasoning
            and "</think>" in text
            and "<think>" not in text
        ):
            text = "<think>\n" + text.lstrip()

        # Only the answer (after the final </think>) needs math normalization;
        # the reasoning block is hidden, so balancing its $$ would be wrong.
        if "</think>" in text:
            head, _, body = text.rpartition("</think>")
            head = self._fix_currency(head + "</think>")
        else:
            head, body = "", text

        return head + self._fix_math(body)

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        try:
            for msg in body.get("messages", []):
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                    msg["content"] = self._normalize(msg["content"])
        except Exception:
            pass
        return body
