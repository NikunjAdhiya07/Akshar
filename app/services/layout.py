"""Group detected lines into paragraphs, headings, and table cells."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict

from app.models import DetectedLine, TextBlock, box_union


def build_blocks(
    lines: list[DetectedLine],
    page_index: int,
    page_width: float,
    page_height: float,
    drawings: list | None = None,
) -> list[TextBlock]:
    usable = _unwrap_nested_lines([line for line in lines if line.text.strip()])
    if not usable:
        return []
    consumed: set[int] = set()
    blocks: list[TextBlock] = []
    table_lines = _tables_from_rules(usable, drawings or [], page_width, page_height)
    for table in table_lines:
        for line_index, _, _, _ in table["cells"]:
            consumed.add(line_index)
        blocks.extend(_blocks_from_table(table, usable, page_index))
    leftover = [line for index, line in enumerate(usable) if index not in consumed]
    alignment_tables, still_free = _tables_from_alignment(leftover, page_width)
    aligned, rejected = _blocks_from_alignment(alignment_tables, page_index, len(blocks))
    blocks.extend(aligned)
    still_free = still_free + rejected
    body = _merge_paragraphs(still_free, page_width)
    sizes = [line.font_size for line in still_free] or [11]
    median = statistics.median(sizes)
    for group in body:
        blocks.append(_paragraph_block(group, page_index, page_width, page_height, median, len(blocks)))
    _assign_draw_boxes(blocks, page_width, page_height)
    blocks.sort(key=lambda block: (block.bbox[1], block.bbox[0]))
    return blocks


def _paragraph_block(
    group: list[DetectedLine],
    page_index: int,
    page_width: float,
    page_height: float,
    median: float,
    number: int,
) -> TextBlock:
    boxes = [line.bbox for line in group]
    bbox = box_union(boxes)
    text = _join_lines(group)
    size = statistics.median([line.font_size for line in group])
    bold = sum(1 for line in group if line.bold) >= max(1, len(group) / 2)
    italic = all(line.italic for line in group)
    color = group[0].color
    confidence = min(line.confidence for line in group)
    kind = "paragraph"
    if bbox[3] < page_height * 0.075:
        kind = "header"
    elif bbox[1] > page_height * 0.925:
        kind = "footer"
    elif _NUMBERED.match(text) and len(text.split()) <= 8:
        kind = "heading"
    elif size >= median * 1.28 and len(text.split()) <= 14:
        kind = "heading"
    if kind == "heading":
        bold = True
    return TextBlock(
        id=f"p{page_index}b{number}",
        page=page_index,
        bbox=bbox,
        draw_bbox=list(bbox),
        source_boxes=[list(box) for box in boxes],
        text=text,
        kind=kind,
        font_size=float(size),
        bold=bold,
        italic=italic,
        align="center" if kind == "heading" else _alignment(group, page_width, bbox),
        valign="middle" if kind == "heading" else "top",
        color=list(color),
        confidence=float(confidence),
        angle=float(group[0].angle),
    )


def _join_lines(group: list[DetectedLine]) -> str:
    parts: list[str] = []
    for line in group:
        piece = line.text.strip()
        if not piece:
            continue
        if parts and parts[-1].endswith("-") and piece[:1].islower():
            parts[-1] = parts[-1][:-1] + piece
        else:
            parts.append(piece)
    return " ".join(parts)


def _alignment(group: list[DetectedLine], page_width: float, bbox: list[float]) -> str:
    if len(group) == 1:
        left = group[0].bbox[0]
        right = page_width - group[0].bbox[2]
        if left > page_width * 0.12 and abs(left - right) < page_width * 0.1:
            return "center"
        if right + 10 < left and left > page_width * 0.5:
            return "right"
        return "left"
    lefts = [line.bbox[0] for line in group]
    rights = [line.bbox[2] for line in group]
    if max(lefts) - min(lefts) <= 6:
        return "left"
    if max(rights) - min(rights) <= 6 and max(lefts) - min(lefts) > 12:
        return "right"
    center = (bbox[0] + bbox[2]) / 2
    if abs(center - page_width / 2) < page_width * 0.08 and bbox[0] > page_width * 0.12:
        return "center"
    return "left"


def _merge_paragraphs(lines: list[DetectedLine], page_width: float) -> list[list[DetectedLine]]:
    ordered = sorted(lines, key=lambda line: ((line.bbox[1] + line.bbox[3]) / 2, line.bbox[0]))
    groups: list[list[DetectedLine]] = []
    for line in ordered:
        merged = False
        for group in reversed(groups[-5:]):
            if _can_merge(group[-1], line, page_width):
                group.append(line)
                merged = True
                break
        if not merged:
            groups.append([line])
    return groups


_FIELD = re.compile(r"^[A-Za-z][A-Za-z .]{0,20}:")
_NUMBERED = re.compile(r"^\d+\.\s+\S")


def _can_merge(previous: DetectedLine, nxt: DetectedLine, page_width: float) -> bool:
    # Labels, addresses, and stacked titles stay on their own rows.
    # Wrapped body lines that share a margin become one paragraph.
    del page_width
    if _FIELD.match(previous.text.strip()) or _FIELD.match(nxt.text.strip()):
        return False
    if _NUMBERED.match(previous.text.strip()):
        return False
    if len(previous.text.split()) <= 3 and len(nxt.text.split()) <= 3:
        return False
    if len(previous.text.split()) <= 4 and len(nxt.text.split()) >= 5 and not nxt.text.strip()[:1].islower():
        return False
    if not _continues_paragraph(previous, nxt):
        return False
    height = max(4.0, previous.bbox[3] - previous.bbox[1])
    gap = nxt.bbox[1] - previous.bbox[3]
    if gap > max(height * 0.7, 7):
        return False
    if gap < -height * 0.45:
        return False
    if abs(previous.angle) > 20 or abs(nxt.angle) > 20:
        return False
    if previous.bold != nxt.bold and len(previous.text.split()) <= 8:
        return False
    bigger = max(previous.font_size, nxt.font_size)
    if abs(previous.font_size - nxt.font_size) > bigger * 0.2:
        return False
    if previous.font_size > nxt.font_size * 1.2 and len(previous.text) < 80:
        return False
    overlap = _x_overlap(previous.bbox, nxt.bbox)
    narrower = min(_width(previous.bbox), _width(nxt.bbox))
    left_close = abs(previous.bbox[0] - nxt.bbox[0]) < max(previous.font_size * 1.15, 10)
    if narrower > 0 and overlap < narrower * 0.35 and not left_close:
        return False
    return True


def _continues_paragraph(previous: DetectedLine, nxt: DetectedLine) -> bool:
    """True when the next line is a wrap of the same paragraph, not a new row."""
    if abs(previous.bbox[0] - nxt.bbox[0]) > max(previous.font_size * 1.4, 12):
        return False
    if nxt.text.strip()[:1].islower():
        return True
    if abs(previous.bbox[2] - nxt.bbox[2]) <= max(14.0, _width(previous.bbox) * 0.12):
        return True
    prev_w = _width(previous.bbox)
    next_w = _width(nxt.bbox)
    return prev_w >= next_w >= prev_w * 0.55 and len(previous.text.split()) >= 6


def _x_overlap(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _width(box: list[float]) -> float:
    return max(0.0, box[2] - box[0])


def _tables_from_rules(lines, drawings, page_width, page_height) -> list[dict]:
    del page_width, page_height
    horizontal, vertical = _rules(drawings)
    tables = []
    groups = _rule_groups(horizontal)
    for group in groups:
        if len(group) < 3:
            continue
        y0 = min(item[2] for item in group) - 1
        y1 = max(item[2] for item in group) + 1
        x0 = min(item[0] for item in group)
        x1 = max(item[1] for item in group)
        verts = [
            item
            for item in vertical
            if item[1] < y1 and item[2] > y0 and x0 - 6 <= item[0] <= x1 + 6
        ]
        xs = _cluster_values([item[0] for item in verts], 2.4)
        ys = _cluster_values([item[2] for item in group], 2.2)
        if len(xs) < 3 or len(ys) < 3:
            continue
        cells = []
        for index, line in enumerate(lines):
            cx = (line.bbox[0] + line.bbox[2]) / 2
            cy = (line.bbox[1] + line.bbox[3]) / 2
            if not (xs[0] - 4 <= cx <= xs[-1] + 4 and ys[0] - 4 <= cy <= ys[-1] + 4):
                continue
            col = _band_index(xs, cx)
            row = _band_index(ys, cy)
            if col is None or row is None:
                continue
            cells.append((index, row, col, line))
        if len({(row, col) for _, row, col, _ in cells}) < 4:
            continue
        tables.append({"id": len(tables), "xs": xs, "ys": ys, "cells": cells})
    return tables


def _rule_groups(horizontal: list[tuple[float, float, float]]) -> list[list[tuple[float, float, float]]]:
    groups: list[list[tuple[float, float, float]]] = []
    for item in sorted(horizontal, key=lambda rule: rule[2]):
        placed = False
        for group in groups:
            ref = group[0]
            overlap = min(item[1], ref[1]) - max(item[0], ref[0])
            span = max(item[1] - item[0], ref[1] - ref[0], 1)
            close = abs(item[2] - group[-1][2]) < 80
            if overlap / span > 0.65 and close:
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    return groups


def _band_index(edges: list[float], value: float) -> int | None:
    for index in range(len(edges) - 1):
        if edges[index] - 2 <= value <= edges[index + 1] + 2:
            return index
    return None


def _rules(drawings) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for drawing in drawings:
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                _collect_rule(horizontal, vertical, p1.x, p1.y, p2.x, p2.y)
            elif kind == "re":
                rect = item[1]
                _collect_rule(horizontal, vertical, rect.x0, rect.y0, rect.x1, rect.y0)
                _collect_rule(horizontal, vertical, rect.x0, rect.y1, rect.x1, rect.y1)
                _collect_rule(horizontal, vertical, rect.x0, rect.y0, rect.x0, rect.y1)
                _collect_rule(horizontal, vertical, rect.x1, rect.y0, rect.x1, rect.y1)
    return horizontal, vertical


def _collect_rule(horizontal, vertical, x0, y0, x1, y1) -> None:
    if abs(y0 - y1) <= 1.6 and abs(x0 - x1) >= 18:
        horizontal.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
    elif abs(x0 - x1) <= 1.6 and abs(y0 - y1) >= 10:
        vertical.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))


def _cluster_values(values: list[float], tolerance: float) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    groups = [[ordered[0]]]
    for value in ordered[1:]:
        if value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _blocks_from_table(table: dict, lines: list[DetectedLine], page_index: int) -> list[TextBlock]:
    grouped: dict[tuple[int, int], list[DetectedLine]] = defaultdict(list)
    for _, row, col, line in table["cells"]:
        grouped[(row, col)].append(line)
    xs = table["xs"]
    ys = table["ys"]
    blocks = []
    number = 0
    for (row, col), cell_lines in sorted(grouped.items()):
        cell_lines = sorted(cell_lines, key=lambda line: line.bbox[1])
        x0, x1 = xs[col] + 3, xs[col + 1] - 3
        y0, y1 = ys[row] + 2, ys[row + 1] - 2
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        text = _join_lines(cell_lines)
        size = statistics.median(line.font_size for line in cell_lines)
        single = len(cell_lines) == 1 and len(text) < 48
        blocks.append(
            TextBlock(
                id=f"p{page_index}t{table['id']}c{number}",
                page=page_index,
                bbox=[x0, y0, x1, y1],
                draw_bbox=[x0, y0, x1, y1],
                source_boxes=[list(line.bbox) for line in cell_lines],
                text=text,
                kind="cell",
                font_size=float(size),
                bold=any(line.bold for line in cell_lines) and len(text.split()) <= 4,
                align="center" if single else "left",
                valign="middle" if single else "top",
                color=list(cell_lines[0].color),
                confidence=min(line.confidence for line in cell_lines),
                table_id=f"p{page_index}-table-{table['id']}",
                row=row,
                col=col,
            )
        )
        number += 1
    return blocks


def _tables_from_alignment(
    lines: list[DetectedLine], page_width: float
) -> tuple[list[list[list[DetectedLine]]], list[DetectedLine]]:
    if len(lines) < 4:
        return [], lines
    heights = [max(4.0, line.bbox[3] - line.bbox[1]) for line in lines]
    tolerance = statistics.median(heights) * 0.55
    rows = _cluster_rows(lines, tolerance)
    tabular = []
    for row in rows:
        gaps = []
        ordered = sorted(row, key=lambda line: line.bbox[0])
        for left, right in zip(ordered, ordered[1:]):
            gaps.append(right.bbox[0] - left.bbox[2])
        wide = any(_width(line.bbox) > page_width * 0.55 for line in ordered)
        if len(ordered) >= 2 and gaps and min(gaps) > 6 and not wide:
            tabular.append(ordered)
        else:
            tabular.append(None)
    runs: list[list[list[DetectedLine]]] = []
    current: list[list[DetectedLine]] = []
    for row, original in zip(tabular, rows):
        if row is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if current:
            previous_y = statistics.mean((line.bbox[1] + line.bbox[3]) / 2 for line in current[-1])
            this_y = statistics.mean((line.bbox[1] + line.bbox[3]) / 2 for line in row)
            if this_y - previous_y > tolerance * 6:
                if len(current) >= 2:
                    runs.append(current)
                current = [row]
                continue
        current.append(row)
    if len(current) >= 2:
        runs.append(current)
    runs = [run for run in runs if _is_data_table(run)]
    used = {id(line) for run in runs for row in run for line in row}
    free = [line for line in lines if id(line) not in used]
    return runs, free


def _is_data_table(rows: list[list[DetectedLine]]) -> bool:
    """Prose set in columns is not a table. Short grid cells are."""
    words = [len(line.text.split()) for row in rows for line in row]
    if not words:
        return False
    return statistics.median(words) <= 4


def _cluster_rows(lines: list[DetectedLine], tolerance: float) -> list[list[DetectedLine]]:
    ordered = sorted(lines, key=lambda line: (line.bbox[1] + line.bbox[3]) / 2)
    rows: list[list[DetectedLine]] = []
    centers: list[float] = []
    for line in ordered:
        center = (line.bbox[1] + line.bbox[3]) / 2
        if rows and abs(center - centers[-1]) <= tolerance:
            rows[-1].append(line)
            centers[-1] = statistics.mean((item.bbox[1] + item.bbox[3]) / 2 for item in rows[-1])
        else:
            rows.append([line])
            centers.append(center)
    return rows


def _blocks_from_alignment(runs, page_index: int, start_number: int):
    blocks: list[TextBlock] = []
    rejected: list[DetectedLine] = []
    number = start_number
    for table_index, rows in enumerate(runs):
        columns = _column_edges(rows)
        if len(columns) < 3:
            rejected.extend(line for row in rows for line in row)
            continue
        row_edges = _row_edges(rows)
        for row_index, row in enumerate(rows):
            for line in row:
                center = (line.bbox[0] + line.bbox[2]) / 2
                col = _band_index(columns, center)
                if col is None:
                    rejected.append(line)
                    continue
                x0, x1 = columns[col] + 2, columns[col + 1] - 2
                y0, y1 = row_edges[row_index] + 1.5, row_edges[row_index + 1] - 1.5
                line_top, line_bottom = line.bbox[1], line.bbox[3]
                if y1 - y0 < (line_bottom - line_top) * 0.8:
                    y0, y1 = line_top, line_bottom
                single = len(line.text) < 48
                blocks.append(
                    TextBlock(
                        id=f"p{page_index}a{number}",
                        page=page_index,
                        bbox=[x0, y0, x1, y1],
                        draw_bbox=[x0, y0, x1, y1],
                        source_boxes=[list(line.bbox)],
                        text=line.text.strip(),
                        kind="cell",
                        font_size=line.font_size,
                        bold=line.bold and len(line.text.split()) <= 4,
                        align="center" if single else "left",
                        valign="middle" if single else "top",
                        color=list(line.color),
                        confidence=line.confidence,
                        table_id=f"p{page_index}-align-{table_index}",
                        row=row_index,
                        col=col,
                    )
                )
                number += 1
    return blocks, rejected


def _column_edges(rows: list[list[DetectedLine]]) -> list[float]:
    centers = [(line.bbox[0] + line.bbox[2]) / 2 for row in rows for line in row]
    mids = _cluster_values(centers, 14)
    if len(mids) < 2:
        return []
    left = min(line.bbox[0] for row in rows for line in row) - 2
    right = max(line.bbox[2] for row in rows for line in row) + 2
    edges = [left]
    for a, b in zip(mids, mids[1:]):
        edges.append((a + b) / 2)
    edges.append(right)
    return edges


def _row_edges(rows: list[list[DetectedLine]]) -> list[float]:
    tops = [min(line.bbox[1] for line in row) for row in rows]
    bottoms = [max(line.bbox[3] for line in row) for row in rows]
    edges = [tops[0] - 1]
    for bottom, top in zip(bottoms, tops[1:]):
        edges.append((bottom + top) / 2)
    edges.append(bottoms[-1] + 1)
    return edges


def _unwrap_nested_lines(lines: list[DetectedLine]) -> list[DetectedLine]:
    """Split a tall OCR box that swallowed the line underneath it."""
    kept = list(lines)
    for small in sorted(lines, key=lambda line: _area(line.bbox)):
        if small not in kept:
            continue
        for big in list(kept):
            if big is small or big not in kept:
                continue
            if _area(big.bbox) <= _area(small.bbox):
                continue
            if not _shares_band(small.bbox, big.bbox):
                continue
            phrase = small.text.strip()
            if phrase.casefold() not in big.text.casefold():
                continue
            trimmed = re.sub(re.escape(phrase), "", big.text, count=1, flags=re.I)
            trimmed = re.sub(r"\s+", " ", trimmed).strip(" -•|&")
            if not any(ch.isalnum() for ch in trimmed):
                kept.remove(big)
                continue
            big.text = trimmed
            if small.bbox[1] >= (big.bbox[1] + big.bbox[3]) / 2:
                big.bbox[3] = min(big.bbox[3], small.bbox[1] - 0.4)
            elif small.bbox[3] <= (big.bbox[1] + big.bbox[3]) / 2:
                big.bbox[1] = max(big.bbox[1], small.bbox[3] + 0.4)
            big.font_size = max(6.0, (big.bbox[3] - big.bbox[1]) * 0.72)
    return kept


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _shares_band(inner: list[float], outer: list[float]) -> bool:
    """True when a shorter line sits inside, or starts inside, a taller line."""
    overlap = _x_overlap(inner, outer)
    if overlap < 0.45 * _width(inner):
        return False
    starts_inside = outer[1] + 1 < inner[1] < outer[3] - 0.4
    return starts_inside or _mostly_inside(inner, outer)


def _mostly_inside(inner: list[float], outer: list[float]) -> bool:
    width = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    height = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    area = _area(inner)
    return area > 0 and (width * height) / area > 0.55


def _assign_draw_boxes(blocks: list[TextBlock], page_width: float, page_height: float) -> None:
    for block in blocks:
        if block.kind == "cell":
            block.draw_bbox = box_union(block.source_boxes) if block.preserved else list(block.bbox)
            continue
        x0, y0, x1, y1 = block.bbox
        block.draw_bbox = [
            max(2, x0),
            max(2, y0),
            min(page_width - 2, x1),
            min(page_height - 2, y1),
        ]
    _give_lines_the_column(blocks, page_width)
    _fit_line_height(blocks)
    _use_gap_below(blocks)
    _separate_draw_boxes(blocks)


def _give_lines_the_column(blocks: list[TextBlock], page_width: float) -> None:
    """Let a short line use the same right margin as the paragraph under it.

    Gujarati is often wider than the English it replaces. A header that only
    occupied the width of "To:" still has to stay inside the letter frame.
    """
    for block in blocks:
        if block.kind == "cell" or block.align == "center":
            continue
        x0, y0, x1, y1 = block.draw_bbox
        if (x1 - x0) > page_width * 0.55 and (y1 - y0) > block.font_size * 2.2:
            continue
        limit = page_width - 8
        for other in blocks:
            if other is block:
                continue
            ox0, oy0, _, oy1 = other.draw_bbox
            overlap_y = min(y1, oy1) - max(y0, oy0)
            if overlap_y > 1.5 and ox0 >= x1 - 1:
                limit = min(limit, ox0 - 2)
        column = x1
        slack = max(18.0, block.font_size * 1.4)
        for other in blocks:
            if abs(other.bbox[0] - block.bbox[0]) <= slack:
                column = max(column, other.bbox[2])
        target = min(limit, column)
        if target > x1 + 4:
            block.draw_bbox[2] = target


def _fit_line_height(blocks: list[TextBlock]) -> None:
    """A short OCR box still has to be tall enough for its own font size."""
    for block in blocks:
        if block.kind == "cell" or len(block.source_boxes) != 1:
            continue
        x0, y0, x1, y1 = block.draw_bbox
        needed = max(y1 - y0, block.font_size * 1.05)
        if y1 - y0 >= needed - 0.3:
            continue
        floor = None
        ceiling = None
        for other in blocks:
            if other is block:
                continue
            ox0, oy0, ox1, oy1 = other.draw_bbox
            if min(x1, ox1) - max(x0, ox0) < 6:
                continue
            if oy0 >= y1 - 0.4 and (floor is None or oy0 < floor):
                floor = oy0
            if oy1 <= y0 + 0.4 and (ceiling is None or oy1 > ceiling):
                ceiling = oy1
        room_below = (floor - y1) if floor is not None else needed
        grow = min(max(0.0, needed - (y1 - y0)), max(0.0, room_below - 1.2))
        block.draw_bbox[3] = y1 + grow
        still = needed - (block.draw_bbox[3] - y0)
        if still > 0.4 and ceiling is not None:
            block.draw_bbox[1] = max(ceiling + 1.2, y0 - still)


def _use_gap_below(blocks: list[TextBlock]) -> None:
    """Give a paragraph the blank space under it, stopping before the next line."""
    for block in blocks:
        if block.kind == "cell" or len(block.source_boxes) < 2:
            continue
        x0, y0, x1, y1 = block.draw_bbox
        nxt = None
        for other in blocks:
            if other is block:
                continue
            ox0, oy0, ox1, _oy1 = other.draw_bbox
            if oy0 < y1 - 0.5:
                continue
            if min(x1, ox1) - max(x0, ox0) < 8:
                continue
            if nxt is None or oy0 < nxt:
                nxt = oy0
        if nxt is None:
            continue
        room = nxt - y1
        if room < 3:
            continue
        block.draw_bbox[3] = y1 + min(room * 0.55, max(block.font_size * 1.1, 4))


def _separate_draw_boxes(blocks: list[TextBlock]) -> None:
    """Give overlapping lines their own vertical band so Gujarati cannot stack."""
    ordered = sorted(blocks, key=lambda block: (block.draw_bbox[1], block.draw_bbox[0]))
    for _ in range(3):
        changed = False
        for index, upper in enumerate(ordered):
            for lower in ordered[index + 1 :]:
                if lower.draw_bbox[1] >= upper.draw_bbox[3] - 0.4:
                    continue
                shared = min(upper.draw_bbox[2], lower.draw_bbox[2]) - max(upper.draw_bbox[0], lower.draw_bbox[0])
                if shared < 8:
                    continue
                top = max(upper.draw_bbox[1], lower.draw_bbox[1])
                bottom = min(upper.draw_bbox[3], lower.draw_bbox[3])
                mid = (top + bottom) / 2
                if upper.draw_bbox[3] > mid:
                    upper.draw_bbox[3] = mid - 0.35
                    changed = True
                if lower.draw_bbox[1] < mid:
                    lower.draw_bbox[1] = mid + 0.35
                    changed = True
        if not changed:
            break
