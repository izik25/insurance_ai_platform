"""Reconstruct visual reading order from PyMuPDF word bounding boxes.

Works around a PyMuPDF/RTL stream-order quirk seen on some PDF generators:
`page.get_text()`'s plain-text stream can emit words of a single visual line
out of order when the line mixes RTL text with an LTR run (e.g. digits) -
confirmed on a real document where "מספר נספח 531" (visual reading order)
came out of get_text() as "531 נספח מספר" (stream order). Individual words
are spelled correctly; only their emission order is wrong, so this is a pure
text/geometry fix, not an OCR concern.
"""

from __future__ import annotations

Word = tuple[float, float, float, float, str, int, int, int]


def reconstruct_rtl_line_order(words: list[Word], y_tolerance: float = 3.0) -> str:
    """Reconstruct visual reading order for a set of words (e.g. from `PdfDocument.extract_words`).

    Clusters words into visual lines purely by y-proximity (ignores
    PyMuPDF's own block_no/line_no grouping, since that's derived from the
    same untrustworthy stream), then sorts each line's words by x0
    descending (right-to-left) to reconstruct true reading order. Lines are
    joined top-to-bottom in the returned string.
    """
    if not words:
        return ""

    ordered = sorted(words, key=lambda w: w[1])
    lines: list[list[Word]] = []
    current: list[Word] = [ordered[0]]
    current_y = ordered[0][1]
    for word in ordered[1:]:
        if abs(word[1] - current_y) <= y_tolerance:
            current.append(word)
        else:
            lines.append(current)
            current = [word]
            current_y = word[1]
    lines.append(current)

    return "\n".join(
        " ".join(word[4] for word in sorted(line, key=lambda w: -w[0])) for line in lines
    )
