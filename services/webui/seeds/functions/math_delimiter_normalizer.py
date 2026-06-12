r"""
title: Math Delimiter Normalizer
author: yannik
version: 0.2.2
required_open_webui_version: 0.5.0
description: Repairs LaTeX/Markdown math so KaTeX renders reliably, WITHOUT touching reasoning. Protects the reasoning block (<details type="reasoning">…</details>, or a folded <think>…</think>) and only normalizes the answer: converts \[ \] -> $$ and \( \) -> $, puts every $$…$$ display block on its own lines with blank-line separation (marked/KaTeX breaks on inline or unspaced $$), repairs mis-escaped currency (\\$ -> \$). Folds an orphaned </think> (no opener) into a collapsible block. Code/inline-code spans are left untouched. Idempotent. Does NOT append $$ to "balance" — that flips correct answers when reasoning has odd $$.
"""
import re
from pydantic import BaseModel, Field

# End markers of a reasoning region. Everything up to and including the LAST
# of these is treated as reasoning and left untouched; only the trailing
# answer is normalized. Open WebUI renders separated reasoning as
# <details type="reasoning">…</details>; older/unparsed output uses <think>.
_REASONING_END_TAGS = ("</details>", "</think>")


class Filter:
    class Valves(BaseModel):
        fold_orphan_reasoning: bool = Field(
            default=True,
            description="Re-add a leading <think> when output has a closing </think> but no opener and no <details> block (Qwen3.5+ template style without a reasoning parser), so Open WebUI folds the chain-of-thought instead of rendering it as prose.",
        )
        normalize_bracket_delims: bool = Field(
            default=True, description=r"Convert \[ \] -> $$ display and \( \) -> $ inline (answer only)"
        )
        blockify_display_math: bool = Field(
            default=True,
            description="Put each $$…$$ display block on its own lines with blank-line separation, so marked/KaTeX never sees an inline or unspaced $$ (answer only).",
        )
        repair_currency: bool = Field(
            default=True, description=r"Collapse mis-escaped currency \\$<digit> -> \$<digit> so it doesn't open math",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _fix_currency(self, text: str) -> str:
        # Two-or-more backslashes before a currency dollar is always a mis-escape:
        # `\\$10` renders as a literal backslash followed by a math-opening `$`.
        # Collapse to a single escaped dollar. Anchored to a digit so real math
        # (e.g. a trailing `\\` line break before `$$`) is never touched. Safe to
        # apply everywhere (reasoning included).
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

        if self.valves.blockify_display_math:
            # Set every balanced $$…$$ block off on its own lines, separated by
            # blank lines. marked/KaTeX mis-render a $$ that is inline with text
            # or lacks line breaks. Only matched (even) pairs are touched, so a
            # stray single $$ is left as-is (never corrupted). Single $…$ inline
            # math has no `$$` and is untouched. Collapsing 3+ newlines keeps it
            # idempotent.
            text = re.sub(
                r"\$\$(.+?)\$\$",
                lambda m: f"\n\n$$\n{m.group(1).strip()}\n$$\n\n",
                text,
                flags=re.S,
            )
            text = re.sub(r"\n{3,}", "\n\n", text)

        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    def _reasoning_cut(self, text: str) -> int:
        """Index just past the last reasoning-end tag, or 0 if none."""
        cut = 0
        for tag in _REASONING_END_TAGS:
            i = text.rfind(tag)
            if i != -1:
                cut = max(cut, i + len(tag))
        return cut

    def _normalize(self, text: str) -> str:
        if not text or "\x00" in text:
            return text

        # Currency repair is a literal-escape fix; safe across the whole message.
        text = self._fix_currency(text)

        # Fold a leaked, half-open reasoning block (closing </think> with no
        # opener and no <details>): re-add <think> so Open WebUI collapses it
        # instead of rendering the chain-of-thought (with its stray $/$$) as
        # prose. Only relevant when no reasoning parser separated it already.
        if (
            self.valves.fold_orphan_reasoning
            and "<details" not in text
            and "</think>" in text
            and "<think>" not in text
        ):
            text = "<think>\n" + text.lstrip()

        # Normalize ONLY the answer after the reasoning region. The reasoning
        # block is hidden and may legitimately carry odd/unbalanced $$ —
        # touching it (e.g. counting its $$) corrupts a correct answer.
        cut = self._reasoning_cut(text)
        head, body = text[:cut], text[cut:]
        return head + self._fix_math(body)

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        try:
            for msg in body.get("messages", []):
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                    msg["content"] = self._normalize(msg["content"])
        except Exception:
            pass
        return body
