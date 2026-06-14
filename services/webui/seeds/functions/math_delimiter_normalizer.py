r"""
title: Math Delimiter Normalizer
author: yannik
version: 0.3.0
required_open_webui_version: 0.5.0
description: Repairs LaTeX/Markdown math so KaTeX renders reliably. Converts \[ \] -> $$ and \( \) -> $ across the WHOLE message (answer + reasoning) so inline \( \) math renders everywhere (Markdown eats the backslash, so \( \) never renders raw). The count-sensitive fixes stay answer-only (the reasoning block may carry odd/unbalanced $$): puts every $$…$$ display block on its own lines with blank-line separation, trims spaces inside inline $…$ (math spans only), escapes bare | inside math within markdown table cells, repairs mis-escaped currency (\\$ -> \$). Folds an orphaned </think> (no opener) into a collapsible block. Code/inline-code spans and table rows are protected. Idempotent. Does NOT append $$ to "balance".
"""
import re
from pydantic import BaseModel, Field

# End markers of a reasoning region. Everything up to and including the LAST
# of these is treated as reasoning and left untouched; only the trailing
# answer is normalized. Open WebUI renders separated reasoning as
# <details type="reasoning">…</details>; older/unparsed output uses <think>.
_REASONING_END_TAGS = ("</details>", "</think>")

# An unclosed inline/display opener: marks where the streaming converter must
# stop emitting and hold the rest until its partner arrives in a later chunk.
# (?<!\\) excludes a row-break + bracket (`\\[4pt]`) — not a real opener.
_OPENER_RE = re.compile(r"(?<!\\)\\\(|(?<!\\)\\\[")


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
        escape_table_cell_pipes: bool = Field(
            default=True,
            description="In markdown table rows, escape a bare | inside $…$/$$…$$ to \\| so the table parser does not split the cell mid-math (e.g. \\mathbb{E}|X_n| ).",
        )
        repair_currency: bool = Field(
            default=True, description=r"Collapse mis-escaped currency \\$<digit> -> \$<digit> so it doesn't open math",
        )
        tighten_inline_delims: bool = Field(
            default=True,
            description=r"Trim spaces just inside inline $…$ ($ x$ / $x $ -> $x$) so KaTeX renders it, but ONLY when the span contains a \command, so currency/prose dollars are never touched.",
        )
        convert_during_stream: bool = Field(
            default=True,
            description=r"Convert \( \)->$ and \[ \]->$$ LIVE during streaming (buffered across token chunks) so inline math renders as it streams instead of only after the outlet pass (which needs a reload to re-render). Never drops content.",
        )

    def __init__(self):
        self.valves = self.Valves()
        # Per-response carry buffer for the streaming converter, keyed by the
        # completion id so concurrent streams never mix. Holds only an unclosed
        # math tail; flushed on the finish chunk (see _stream_step).
        self._stream_bufs = {}

    def _fix_currency(self, text: str) -> str:
        # Two-or-more backslashes before a currency dollar is always a mis-escape:
        # `\\$10` renders as a literal backslash followed by a math-opening `$`.
        # Collapse to a single escaped dollar. Anchored to a digit so real math
        # (e.g. a trailing `\\` line break before `$$`) is never touched. Safe to
        # apply everywhere (reasoning included).
        if not self.valves.repair_currency:
            return text
        return re.sub(r"\\{2,}\$(?=\d)", r"\\$", text)

    @staticmethod
    def _escape_pipes_in_math(line: str) -> str:
        # Within one markdown table row, escape a bare `|` that sits inside a
        # math span ($…$ or $$…$$) to `\|`. GFM treats `\|` as a literal pipe
        # in a cell, so the cell no longer splits mid-math. `(?<!\\)` keeps it
        # idempotent (an already-escaped `\|` is not touched). Cell-delimiter
        # `|` (outside any math span) is left alone.
        def esc(m):
            return re.sub(r"(?<!\\)\|", r"\\|", m.group(0))

        line = re.sub(r"\$\$.+?\$\$", esc, line)
        line = re.sub(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", esc, line)
        return line

    def _fix_math(self, text: str) -> str:
        # Protect fenced + inline code so we never rewrite math-looking code.
        stash = []

        def _hide(m):
            stash.append(m.group(0))
            return f"\x00{len(stash) - 1}\x00"

        text = re.sub(r"```.*?```", _hide, text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", _hide, text)

        if self.valves.normalize_bracket_delims:
            # (?<!\\) so a row-break + bracket like `\\[4pt]` inside a cases/align
            # block is NOT mistaken for a \[ \] display delimiter (it would mangle
            # the whole equation). A real opener is a single backslash + bracket.
            text = re.sub(
                r"(?<!\\)\\\[\s*(.+?)\s*\\\]",
                lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n",
                text,
                flags=re.S,
            )
            text = re.sub(
                r"(?<!\\)\\\(\s*(.+?)\s*\\\)",
                lambda m: f"${m.group(1).strip()}$",
                text,
                flags=re.S,
            )

        # Stash whole table rows BEFORE blockify so display-math reflow can't
        # tear a table apart, and escape any bare `|` inside their math cells.
        if self.valves.blockify_display_math or self.valves.escape_table_cell_pipes:
            def _table_row(m):
                line = m.group(0)
                if self.valves.escape_table_cell_pipes:
                    line = self._escape_pipes_in_math(line)
                stash.append(line)
                return f"\x00{len(stash) - 1}\x00"

            text = re.sub(r"^[ \t]*\|.*\|[ \t]*$", _table_row, text, flags=re.M)

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

        if self.valves.tighten_inline_delims:
            # KaTeX skips `$ x$` / `$x $` (a space adjacent to the delimiter). Trim
            # the inner edge spaces, but ONLY for spans containing a \command so
            # prose/currency dollars ("$ 5 and $ 10") are never matched. `$$` is
            # excluded via the lookbehind/lookahead; idempotent (no-op once tight).
            text = re.sub(
                r"(?<![$\\])\$(?!\$)[ \t]*((?=[^$\n]*\\)[^$\n]+?)[ \t]*\$(?!\$)",
                lambda m: f"${m.group(1).strip()}$",
                text,
            )

        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    def _swap_brackets(self, text: str) -> str:
        # Convert \[ \] -> $$ (own lines) and \( \) -> $ across the WHOLE message
        # (answer AND reasoning). Unlike $$ blockify/balance this is a literal,
        # count-independent delimiter swap, so it's safe in the reasoning region
        # too. Markdown eats the backslash in \( \), so KaTeX never renders it;
        # converting to $ (which survives markdown) is what makes inline math show.
        # Code / inline-code spans are protected. Idempotent.
        if not self.valves.normalize_bracket_delims:
            return text
        stash = []

        def _hide(m):
            stash.append(m.group(0))
            return f"\x00{len(stash) - 1}\x00"

        text = re.sub(r"```.*?```", _hide, text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", _hide, text)
        # (?<!\\): never treat a `\\[`/`\\(` (row-break + bracket, e.g. `\\[4pt]`)
        # as a delimiter — only a single-backslash opener is real.
        text = re.sub(r"(?<!\\)\\\[\s*(.+?)\s*\\\]", lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n", text, flags=re.S)
        text = re.sub(r"(?<!\\)\\\(\s*(.+?)\s*\\\)", lambda m: f"${m.group(1).strip()}$", text, flags=re.S)
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

        # Bracket swap (\[ \] -> $$, \( \) -> $) runs across the WHOLE message so
        # inline math renders even inside the (expandable) reasoning block. This
        # is the fix for models like DeepSeek/Qwen that emit \( \) for inline math.
        text = self._swap_brackets(text)

        # The count-sensitive fixes (blockify $$ / table-pipe escape / inline-$
        # trim) stay ANSWER-ONLY: the reasoning block may carry odd/unbalanced $$,
        # and touching it (e.g. reflowing/counting) can corrupt a correct answer.
        cut = self._reasoning_cut(text)
        head, body = text[:cut], text[cut:]
        return head + self._fix_math(body)

    def stream(self, event: dict) -> dict:
        # LIVE (during-stream) fix: convert a COMPLETE inline \( … \) inside a
        # single streamed chunk to $ … $, so inline math renders as it streams
        # instead of only after the outlet pass (which otherwise needs a page
        # reload to show). Inline math is short and almost always arrives whole
        # in one chunk; a \( … \) split across chunks is left for outlet to
        # finish. Display \[ … \] is left to outlet (it already renders, and
        # mid-stream blockify is not worth the risk). Cheap-guarded; never raises.
        if not self.valves.normalize_bracket_delims:
            return event
        try:
            for ch in event.get("choices", []):
                delta = ch.get("delta", {})
                c = delta.get("content")
                if isinstance(c, str) and "\\(" in c:
                    delta["content"] = re.sub(
                        r"\\\(\s*(.+?)\s*\\\)",
                        lambda m: f"${m.group(1).strip()}$",
                        c,
                        flags=re.S,
                    )
        except Exception:
            pass
        return event

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        try:
            for msg in body.get("messages", []):
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                    msg["content"] = self._normalize(msg["content"])
        except Exception:
            pass
        return body

    def _stream_step(self, sid: str, c: str, final: bool) -> str:
        # Accumulate this chunk onto the carry buffer, convert every COMPLETE
        # \[ \]->$$ / \( \)->$ pair, then emit everything up to the first still-
        # open \( or \[ and carry the rest forward. Net result: an inline span
        # split across tokens (\( · x · \)) is converted before it is shown, so
        # KaTeX renders it live instead of waiting for the reload-only outlet.
        buf = self._stream_bufs.get(sid, "") + c
        buf = re.sub(
            r"(?<!\\)\\\[\s*(.+?)\s*\\\]", lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n", buf, flags=re.S
        )
        buf = re.sub(r"(?<!\\)\\\(\s*(.+?)\s*\\\)", lambda m: f"${m.group(1).strip()}$", buf, flags=re.S)
        if final:
            # Last chunk of the response: flush everything, never hold content.
            self._stream_bufs.pop(sid, None)
            return buf
        m = _OPENER_RE.search(buf)
        cut = m.start() if m else len(buf)
        # A lone trailing backslash may be the first half of \( or \[ — hold it.
        if cut == len(buf) and buf.endswith("\\"):
            cut = len(buf) - 1
        out, held = buf[:cut], buf[cut:]
        # Safety: never carry an unbounded tail (a never-closed opener, or non-math
        # backslash). Past the cap, give up holding and emit it raw — the outlet
        # still fixes the stored copy. Guarantees no content is ever stuck/lost.
        if len(held) > 2000:
            out, held = out + held, ""
        self._stream_bufs[sid] = held
        if len(self._stream_bufs) > 64:  # bound memory if finish chunks go missing
            self._stream_bufs = {sid: held}
        return out

    def stream(self, event: dict) -> dict:
        # Live, per-chunk delimiter conversion. Open WebUI applies this to each
        # upstream SSE delta (often one token) BEFORE batching, so it must be
        # stateful — see _stream_step. Touches only delta["content"]; role/tool
        # deltas and $…$/$$…$$ (already render live) pass through untouched. Any
        # error degrades to passthrough so streaming is never broken.
        if not self.valves.convert_during_stream:
            return event
        try:
            sid = event.get("id") or "_"
            for ch in event.get("choices", []):
                delta = ch.get("delta")
                if not isinstance(delta, dict):
                    continue
                c = delta.get("content")
                final = ch.get("finish_reason") is not None
                if not isinstance(c, str):
                    c = ""
                if c == "" and not final:
                    continue
                delta["content"] = self._stream_step(sid, c, final)
                if final:
                    self._stream_bufs.pop(sid, None)
        except Exception:
            pass
        return event
