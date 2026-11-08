"""
Independent checks of a cutting plan, written separately from the solvers: every piece cut exactly as ordered, every
bar and sheet within its length or size after trim and saw kerf, no overlaps, panels turned only when allowed, sheet
layouts cuttable with straight end-to-end (guillotine) cuts, and no more stock used than is available.
"""

from __future__ import annotations

from collections import Counter

EPS = 1e-6


def check_bars(job, bars) -> list:
    problems = []
    got = Counter(i for _, ps in bars for i in ps)
    for i, p in enumerate(job.pieces):
        if got[i] != p.qty:
            problems.append(f"{p.name}: {got[i]} cut, {p.qty} ordered")
    used = Counter(s for s, _ in bars)
    for s, b in enumerate(job.stock):
        if b.available is not None and used[s] > b.available:
            problems.append(f"{b.name}: {used[s]} used, {b.available} available")
    for k, (s, ps) in enumerate(bars):
        need = sum(job.pieces[i].length for i in ps) + job.kerf * max(0, len(ps) - 1)
        room = job.stock[s].length - job.trim
        if need > room + EPS:
            problems.append(f"bar {k + 1} ({job.stock[s].name}): pieces and cuts need {need:g}, only {room:g} usable")
    return problems


def _overlap(a, b, gap):
    """Rectangles (x, y, w, h) closer than the kerf (or overlapping)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw + gap - EPS and bx < ax + aw + gap - EPS and
            ay < by + bh + gap - EPS and by < ay + ah + gap - EPS)


def guillotine(rects, x0, y0, x1, y1):
    """Can these rectangles be separated by straight end-to-end cuts, recursively?"""
    if len(rects) <= 1:
        return True
    for axis in (0, 1):
        lo = sorted(set([r[axis] for r in rects] + [r[axis] + r[axis + 2] for r in rects]))
        for c in lo:
            left = [r for r in rects if r[axis] + r[axis + 2] <= c + EPS]
            right = [r for r in rects if r[axis] >= c - EPS]
            if left and right and len(left) + len(right) == len(rects):
                if axis == 0:
                    return guillotine(left, x0, y0, c, y1) and guillotine(right, c, y0, x1, y1)
                return guillotine(left, x0, y0, x1, c) and guillotine(right, x0, c, x1, y1)
    return False


def check_sheets(job, sheets, rectangles) -> list:
    """rectangles(job, s, layout) -> ([(panel, x, y, w, h, rotated)], cuts, (W, H))."""
    problems = []
    got = Counter()
    used = Counter(s for s, _ in sheets)
    for s, sh in enumerate(job.sheets):
        if sh.available is not None and used[s] > sh.available:
            problems.append(f"{sh.name}: {used[s]} used, {sh.available} available")
    for k, (s, lay) in enumerate(sheets):
        rects, _, (W, H) = rectangles(job, s, lay)
        boxes = []
        for i, x, y, w, h, r in rects:
            p = job.panels[i]
            got[i] += 1
            if r and not p.rotate:
                problems.append(f"sheet {k + 1}: {p.name} turned against the grain")
            exp = (p.height, p.width) if r else (p.width, p.height)
            if abs(w - exp[0]) > EPS or abs(h - exp[1]) > EPS:
                problems.append(f"sheet {k + 1}: {p.name} has the wrong size")
            if x < job.trim - EPS or y < job.trim - EPS or x + w > W - job.trim + EPS or y + h > H - job.trim + EPS:
                problems.append(f"sheet {k + 1}: {p.name} outside the usable sheet")
            boxes.append((x, y, w, h))
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                if _overlap(boxes[a], boxes[b], job.kerf):
                    problems.append(f"sheet {k + 1}: two panels overlap or are closer than the saw kerf")
                    break
        grown = [(x, y, w + job.kerf, h + job.kerf) for x, y, w, h in boxes]
        if not guillotine(grown, 0, 0, W + job.kerf, H + job.kerf):
            problems.append(f"sheet {k + 1}: cannot be cut with straight end-to-end cuts")
    for i, p in enumerate(job.panels):
        if got[i] != p.qty:
            problems.append(f"{p.name}: {got[i]} cut, {p.qty} ordered")
    return problems
