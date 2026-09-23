"""
Intelligent message chunking for messaging platforms.

Operates on already channel-rendered text (after presentation).
Prefer semantic boundaries. Respect platform limits.

Invariants:
  - Prefer paragraph → list-item → sentence → line → word → hard cut.
  - Do not leave an odd number of code-fence markers (```) in a chunk when
    the closing fence is within CODE_FENCE_SLACK of max_chars.
  - Do not split immediately after an opening emphasis/code marker when the
    matching closer is still in the same window.
  - CODE_FENCE_SLACK: a chunk may exceed max_chars by at most this factor
    solely to close an open code fence (explicit provider-limit tradeoff).
"""

from __future__ import annotations

import re
from typing import List

# Explicit: allow extending past max_chars by this factor only to close a code fence.
CODE_FENCE_SLACK = 1.5

# Opening markers that should not be left unpaired in a chunk when closer is nearby.
_EMPHASIS_PAIRS = (
    ("*", "*"),
    ("_", "_"),
    ("`", "`"),
)


def _unbalanced_fence(text: str) -> bool:
    return text.count("```") % 2 == 1


def _has_unclosed_inline_marker(text: str) -> bool:
    """
    Heuristic: odd count of a single-char emphasis/code marker that is not
    part of a fence. Used only to prefer extending the split slightly.
    """
    # Strip fences first so we don't count fence backticks
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    stripped = re.sub(r"```[^\n]*$", "", stripped)  # open fence at end
    for marker, _ in _EMPHASIS_PAIRS:
        # Count standalone markers (not ** which is strong on WhatsApp/Telegram)
        if marker == "*":
            # Count single * not part of **
            singles = re.findall(r"(?<!\*)\*(?!\*)", stripped)
            if len(singles) % 2 == 1:
                return True
        elif marker == "_":
            singles = re.findall(r"(?<!_)_(?!_)", stripped)
            if len(singles) % 2 == 1:
                return True
        elif marker == "`":
            if stripped.count("`") % 2 == 1:
                return True
    return False


def chunk_message(
    text: str,
    *,
    max_chars: int = 3500,
    prefer_paragraphs: bool = True,
) -> List[str]:
    """
    Split long tutor responses into platform-friendly chunks.
    Call after presentation/rendering so formatting constructs are complete.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    hard_ceiling = int(max_chars * CODE_FENCE_SLACK)

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break

        window = remaining[: max_chars + 1]
        split_at = -1

        # 1. Prefer paragraph break
        if prefer_paragraphs:
            para = window.rfind("\n\n")
            if para > max_chars // 3:
                split_at = para

        # 2. List item boundary (line starting with bullet / number after newline)
        if split_at < 0:
            idx = window.rfind("\n")
            while idx > max_chars // 3:
                after = window[idx + 1 : idx + 6]
                if (
                    after.startswith("• ")
                    or re.match(r"\d+\.\s", after)
                    or after.startswith("- ")
                ):
                    split_at = idx
                    break
                idx = window.rfind("\n", 0, idx)

        # 3. Sentence boundary
        if split_at < 0:
            for sep in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                idx = window.rfind(sep)
                if idx > max_chars // 3:
                    split_at = idx + len(sep) - 1
                    break

        # 4. Line break
        if split_at < 0:
            line = window.rfind("\n")
            if line > max_chars // 3:
                split_at = line

        # 5. Word boundary
        if split_at < 0:
            space = window.rfind(" ")
            if space > max_chars // 4:
                split_at = space

        if split_at < 0:
            split_at = max_chars

        piece = remaining[: split_at + 1].strip()
        if piece:
            # Close open code fences within slack
            if _unbalanced_fence(piece):
                close = remaining.find("```", split_at + 1)
                if 0 < close < hard_ceiling:
                    piece = remaining[: close + 3].strip()
                    split_at = close + 2

            # Prefer not leaving unpaired inline markers if closer is nearby
            if _has_unclosed_inline_marker(piece):
                # Search a bit further for a safer boundary
                extended = remaining[: min(len(remaining), hard_ceiling)]
                # Try next paragraph or sentence after current split
                for sep in ["\n\n", ". ", "!\n", "?\n", "\n"]:
                    idx = extended.find(sep, split_at + 1)
                    if idx > 0 and idx < hard_ceiling:
                        candidate = remaining[: idx + len(sep)].strip()
                        if not _has_unclosed_inline_marker(candidate) and not _unbalanced_fence(
                            candidate
                        ):
                            piece = candidate
                            split_at = idx + len(sep) - 1
                            break

            chunks.append(piece)
        remaining = remaining[split_at + 1 :].lstrip()

    return [c for c in chunks if c]
