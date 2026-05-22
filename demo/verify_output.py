"""Verify that a generated application docx preserves the publisher's
format AND placed leaf body content inside the publisher's input cells.

Run after a live demo to spot regressions in formatting fidelity:

    uv run python demo/verify_output.py demo/output/monodukuri_23rd_application.docx

The check walks the docx body in element order, looks for each
``...（XXX字以内）`` leaf heading, finds the next table or paragraph,
and reports whether the cell / paragraph holds non-empty body content
that is *not* the publisher's own instruction text.

Exit code 0 if every leaf is filled; 1 otherwise. Prints a per-leaf
table to stdout so the user can eyeball what's missing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_CHAR_LIMIT_RE = re.compile(r"[（(]\s*[0-9０-９]+\s*字\s*(以内|程度|以下|まで)\s*[）)]")
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _para_text(p_element) -> str:
    return "".join(
        (t.text or "") for t in p_element.iter(f"{_W_NS}t")
    ).strip()


def _next_input_target(p_element):
    """Walk forward from a leaf heading and report what the publisher's
    designated input region is, plus what's been written there.

    Returns one of:
      * ``("table", cell_text)`` — next non-empty sibling is a table; we
        report the contents of the input cell (last row's first cell).
      * ``("paragraph_first", para_text)`` — a non-empty paragraph
        appeared BEFORE the publisher's table. This is the bug we're
        verifying against: body content placed adjacent to (rather than
        inside) the designated input cell.
      * ``("missing", "")`` — no table or paragraph follows the heading.
    """
    sibling = p_element.getnext()
    para_text_before_table: str | None = None
    while sibling is not None:
        tag = sibling.tag.split("}")[-1]
        if tag == "tbl":
            rows = sibling.findall(f"{_W_NS}tr")
            target_row = rows[-1] if len(rows) >= 2 else (rows[0] if rows else None)
            cell_text = ""
            if target_row is not None:
                cells = target_row.findall(f"{_W_NS}tc")
                if cells:
                    cell_text = "".join(
                        (t.text or "") for t in cells[0].iter(f"{_W_NS}t")
                    ).strip()
            if para_text_before_table:
                # Body was put before the table, not inside it.
                return ("paragraph_first", para_text_before_table)
            return ("table", cell_text)
        if tag == "p":
            text = _para_text(sibling)
            if text and para_text_before_table is None:
                para_text_before_table = text
        sibling = sibling.getnext()
    if para_text_before_table:
        return ("paragraph_first", para_text_before_table)
    return ("missing", "")


def _is_publisher_instruction(text: str) -> bool:
    """Heuristic: the publisher's instruction text typically starts with
    "※" or contains characteristic phrasing like "記載してください"."""
    if not text:
        return True
    head = text[:40]
    return (
        text.startswith("※")
        or "記載してください" in text[:80]
        or "してください" in text[:60]
        or "について説明し" in text[:80]
        or head.startswith("（")
        and "について" in head
    )


def verify(docx_path: Path) -> int:
    doc = Document(str(docx_path))
    body = doc.element.body

    leaves: list[tuple[str, str, str]] = []
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag != "p":
            continue
        text = _para_text(child)
        if not text or not _CHAR_LIMIT_RE.search(text):
            continue
        kind, content = _next_input_target(child)
        leaves.append((text, kind, content))

    print(f"Document: {docx_path}")
    print(f"Leaves with 字数制限: {len(leaves)}")
    print()
    print(f"{'STATUS':10} {'CHARS':>5}  HEADING / CONTENT (first 40 chars)")
    print("-" * 100)
    filled = 0
    misplaced = 0
    for heading, kind, content in leaves:
        chars = len(content)
        if kind == "missing":
            status = "MISSING"
        elif kind == "paragraph_first":
            # Body content was put OUTSIDE the designated table cell.
            # The cell remains empty (or holds the publisher's instruction).
            # Visually this looks "上下に記載" — content above/below the
            # field box rather than in it.
            status = "OUTSIDE_CELL"
            misplaced += 1
        elif chars == 0:
            status = "EMPTY_CELL"
        elif _is_publisher_instruction(content):
            status = "INSTRUCTION_ONLY"
        else:
            status = "OK_IN_CELL"
            filled += 1
        print(f"{status:14} {chars:>5}  {heading[:50]}")
        print(f"{'':14} {'':>5}    → {content[:80].replace(chr(10), ' ')}")
    print()
    print(
        f"Result: {filled}/{len(leaves)} leaves filled correctly "
        f"(inside designated cell); {misplaced} placed outside the cell"
    )
    return 0 if filled == len(leaves) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path, help="検証する .docx ファイル")
    args = parser.parse_args()
    sys.exit(verify(args.docx))


if __name__ == "__main__":
    main()
