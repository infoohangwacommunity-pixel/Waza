"""
Intelligent message chunking for messaging platforms.

Operates on already channel-rendered text.
Never split mid-word, mid-code-block, mid-link, or mid-list-item when avoidable.
Prefer semantic boundaries. Respect platform limits.
"""

from __future__ import annotations

from typing import List


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

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break

        window = remaining[: max_chars + 1]
        split_at = -1

        # Prefer paragraph break
        if prefer_paragraphs:
            para = window.rfind("\n\n")
            if para > max_chars // 3:
                split_at = para

        # List item boundary (line starting with bullet / number after newline)
        if split_at < 0:
            # Find last newline that starts a list-like line within the safe zone
            idx = window.rfind("\n")
            while idx > max_chars // 3:
                after = window[idx + 1 : idx + 4]
                if after.startswith("• ") or after[:2].rstrip().endswith("."):
                    # Prefer splitting *before* this list item
                    split_at = idx
                    break
                idx = window.rfind("\n", 0, idx)

        # Sentence boundary
        if split_at < 0:
            for sep in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
                idx = window.rfind(sep)
                if idx > max_chars // 3:
                    split_at = idx + len(sep) - 1
                    break

        # Line break
        if split_at < 0:
            line = window.rfind("\n")
            if line > max_chars // 3:
                split_at = line

        # Word boundary
        if split_at < 0:
            space = window.rfind(" ")
            if space > max_chars // 4:
                split_at = space

        if split_at < 0:
            split_at = max_chars

        piece = remaining[: split_at + 1].strip()
        if piece:
            # Avoid splitting code fences if possible
            if piece.count("```") % 2 == 1:
                close = remaining.find("```", split_at + 1)
                if 0 < close < max_chars * 1.5:
                    piece = remaining[: close + 3].strip()
                    split_at = close + 2
            # Avoid splitting Telegram/WhatsApp emphasis mid-marker pairs roughly
            chunks.append(piece)
        remaining = remaining[split_at + 1 :].lstrip()

    return [c for c in chunks if c]
