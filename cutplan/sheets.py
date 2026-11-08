"""
Cutting panels from sheets (plywood, MDF, glass, sheet metal) with a panel saw: two-dimensional guillotine cutting.

Every sheet is cut in stages, the way a panel saw or a glass cutter works: rip the sheet into strips across its whole
width, cut each strip into columns, cut each column into the pieces stacked in it, and trim a piece that is smaller
than its slot. A pattern is one such sheet layout. As for bars, the best mix of patterns is found by column
generation: a linear program prices the panels and a three-level knapsack finds the most valuable sheet layout (the
best column for every column width and strip height, the best strip for every height, then the best stack of
strips), in both directions of the sheet. Integer plans come from diving and CP-SAT, like for bars.

Panels may turn 90 degrees unless the grain must run one way. Saw kerf is added between pieces and strips; an edge
trim is taken off all four sides of every sheet first.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from ortools.sat.python import cp_model

from .bars import Master, knapsack


@dataclass
class Panel:
    name: str
    width: float
    height: float
    qty: int
    rotate: bool = True                 # may be turned 90 degrees (no grain direction)


@dataclass
class Sheet:
    name: str
    width: float
    height: float
    cost: float = 1.0
    available: int | None = None


@dataclass
class SheetJob:
    panels: list
    sheets: list
    kerf: float = 0.0
    trim: float = 0.0                   # taken off every edge of a sheet
    name: str = ""

    def scaled(self):
        k = int(round(self.kerf))
        dims = [(int(math.floor(s.width - 2 * self.trim)) + k, int(math.floor(s.height - 2 * self.trim)) + k)
                for s in self.sheets]
        sizes = [(int(math.ceil(p.width)) + k, int(math.ceil(p.height)) + k) for p in self.panels]
        return dims, sizes, k


@dataclass
class SheetPlan:
    sheets: list                        # (sheet index, layout) one per sheet; layout = (transposed, [strip, ...])
    cost: float
    lower_bound: float
    status: str
    seconds: float
    columns: int = 0
    history: list = field(default_factory=list)

    def patterns(self):
        groups = {}
        for s, lay in self.sheets:
            key = (s, _key(lay))
            if key not in groups:
                groups[key] = [s, lay, 0]
            groups[key][2] += 1
        return sorted((tuple(g) for g in groups.values()), key=lambda g: -g[2])


def _key(lay):
    t, strips = lay
    return (t, tuple(sorted((h, tuple(sorted((w, tuple(it)) for w, it in cols))) for h, cols in strips)))


# A layout: (sheet turned?, [strip]); strip: (height, [column]); column: (width, [(panel, rotated), ...] stacked).
# Heights and widths in scaled units, kerf included.

def pieces_of(lay):
    return [(i, r) for _, cols in lay[1] for _, items in cols for i, r in items]


def counts_of(lay, n):
    a = np.zeros(n, int)
    for i, _r in pieces_of(lay):
        a[i] += 1
    return a


# ------------------------------------------------------------------------------------------------ pricing
def best_layout(pi, sizes, rot, bounds, W, H):
    """Most valuable layout of one sheet (W x H scaled, kerf included) at prices pi: strips across the width, columns
    in a strip, pieces stacked in a column (each piece at most as wide as its column). Copies of a column or a strip
    are limited by the panels still needed; strips are added again with what is still needed until nothing more
    fits. Returns (value, layout)."""
    n = len(sizes)
    bounds = np.asarray(bounds, int).copy()
    strips_all = []
    room = H
    for _ in range(4):
        add, used = _fill(pi, sizes, rot, bounds, W, room)
        if not add:
            break
        lay = cap_layout((False, add), bounds, n)
        if not lay[1]:
            break
        strips_all += lay[1]
        bounds = bounds - counts_of(lay, n)
        room = H - sum(h for h, _ in strips_all)
        if room <= 0:
            break
    lay = (False, sorted(strips_all, key=lambda s: -s[0]))
    return float(np.dot(pi, counts_of(lay, n))), lay


def _fill(pi, sizes, rot, bounds, W, H):
    """One pass of the three-level knapsack for the remaining demand `bounds` in a W x H area."""
    n = len(sizes)
    opts = []                                         # (panel, rotated, width, height)
    for i in range(n):
        if pi[i] <= 1e-12 or bounds[i] <= 0:
            continue
        w, h = sizes[i]
        if w <= W and h <= H:
            opts.append((i, False, w, h))
        if rot[i] and w != h and h <= W and w <= H:
            opts.append((i, True, h, w))
    if not opts or H <= 0:
        return [], 0
    heights = sorted({o[3] for o in opts})
    widths = sorted({o[2] for o in opts})
    col_cache = {}

    def column(wc, hs):
        """Best stack of pieces at most wc wide in a column hs high."""
        if (wc, hs) not in col_cache:
            cand = [o for o in opts if o[2] <= wc and o[3] <= hs]
            items, v = [], 0.0
            if cand:
                v, cnt = knapsack([pi[o[0]] for o in cand], [o[3] for o in cand], [int(bounds[o[0]]) for o in cand], hs)
                for o, k in zip(cand, cnt):
                    items += [(o[0], o[1])] * int(k)
            col_cache[(wc, hs)] = (v, items)
        return col_cache[(wc, hs)]

    def copies(items):
        """How many copies of this content the remaining demand allows."""
        c = np.bincount([i for i, _ in items], minlength=n)
        nz = np.nonzero(c)[0]
        return int(min(bounds[i] // c[i] for i in nz)) if len(nz) else 0

    strip_val, strip_cols = {}, {}
    for hs in heights:
        ws = [wc for wc in widths if column(wc, hs)[0] > 1e-12 and copies(column(wc, hs)[1]) > 0]
        if not ws:
            strip_val[hs], strip_cols[hs] = 0.0, []
            continue
        v, cnt = knapsack([column(wc, hs)[0] for wc in ws], ws,
                          [min(W // wc, copies(column(wc, hs)[1])) for wc in ws], W)
        cols = []
        for wc, k in zip(ws, cnt):
            cols += [(wc, list(column(wc, hs)[1]))] * int(k)
        strip_val[hs], strip_cols[hs] = v, cols
    hs_list = [hs for hs in heights if strip_val[hs] > 1e-12]
    if not hs_list:
        return [], 0
    bnd = [max(1, min(H // h, copies([it for _, its in strip_cols[h] for it in its]))) for h in hs_list]
    v, cnt = knapsack([strip_val[h] for h in hs_list], hs_list, bnd, H)
    strips = []
    for h, k in zip(hs_list, cnt):
        strips += [(h, [(wc, list(it)) for wc, it in strip_cols[h]])] * int(k)
    return strips, sum(h for h, _ in strips)


def cap_layout(lay, bounds, n):
    """Remove pieces beyond the demand (they would only be waste), from the emptiest strips first."""
    t, strips = lay
    a = counts_of(lay, n)
    extra = np.maximum(0, a - np.asarray(bounds, int))
    out = []
    for h, cols in sorted(strips, key=lambda s: sum(len(it) for _, it in s[1])):
        new_cols = []
        for wc, items in cols:
            keep = []
            for i, r in items:
                if extra[i] > 0:
                    extra[i] -= 1
                else:
                    keep.append((i, r))
            if keep:
                new_cols.append((wc, keep))
        if new_cols:
            out.append((h, new_cols))
    return (t, sorted(out, key=lambda s: -s[0]))


def price(pi, sizes, rot, bounds, dims_s):
    """Best layout for one sheet in either direction (strips along the width or along the height)."""
    W, H = dims_s
    v1, l1 = best_layout(pi, sizes, rot, bounds, W, H)
    tsizes = [(h, w) for w, h in sizes]
    v2, l2 = best_layout(pi, tsizes, rot, bounds, H, W)
    if v2 > v1 + 1e-12:
        return v2, (True, l2[1])
    return v1, l1


# ------------------------------------------------------------------------------------------------ simple plans
def shelf_layouts(job: SheetJob, demand=None, left=None, rotate_to_fit=True, decreasing=True):
    """What a careful cutter does by hand: tallest panels first, rip a strip as tall as the first panel, fill it left
    to right, start a new strip (or a new sheet) when it is full (first fit decreasing height). With decreasing=False
    the panels are cut in the order of the list."""
    dims, sizes, k = job.scaled()
    n = len(sizes)
    demand = [p.qty for p in job.panels] if demand is None else list(demand)
    left = [s.available for s in job.sheets] if left is None else list(left)
    items = []
    for i in range(n):
        w, h = sizes[i]
        r = False
        if job.panels[i].rotate and rotate_to_fit and h > w:
            w, h, r = h, w, True                      # lay panels flat: lower strips
        items += [(i, r, w, h)] * int(demand[i])
    if decreasing:
        items.sort(key=lambda t: (-t[3], -t[2]))
    out = []                                          # [sheet, strips, used height, [strip used widths]]
    for i, r, w, h in items:
        placed = False
        for sh in out:
            s, strips, used, widths = sh
            W, H = dims[s]
            for k2, (hs, its) in enumerate(strips):
                if h <= hs and widths[k2] + w <= W:
                    its.append((w, [(i, r)]))
                    widths[k2] += w
                    placed = True
                    break
            if placed:
                break
            if used + h <= H and w <= W:
                strips.append((h, [(w, [(i, r)])]))
                widths.append(w)
                sh[2] += h
                placed = True
                break
        if placed:
            continue
        cands = [s for s in range(len(dims)) if dims[s][0] >= w and dims[s][1] >= h and (left[s] is None or left[s] > 0)]
        if not cands and job.panels[i].rotate:
            cands = [s for s in range(len(dims)) if dims[s][0] >= h and dims[s][1] >= w and (left[s] is None or left[s] > 0)]
            if cands:
                w, h, r = h, w, not r
        if not cands:
            return None
        s = min(cands, key=lambda s: job.sheets[s].cost / (dims[s][0] * dims[s][1]))
        if left[s] is not None:
            left[s] -= 1
        out.append([s, [(h, [(w, [(i, r)])])], h, [w]])
    return [(s, (False, strips)) for s, strips, _, _ in out]


# ------------------------------------------------------------------------------------------------ lower bound
def lower_bound(job: SheetJob) -> int:
    """Sheets needed whatever the cutting method (single sheet size, unlimited): area bound, and panels too large
    to share a sheet with each other (both sides more than half the sheet's)."""
    dims, sizes, k = job.scaled()
    if len(dims) != 1 or job.sheets[0].available is not None:
        return 0
    W, H = dims[0]
    area = sum((w - k) * (h - k) * p.qty for (w, h), p in zip(sizes, job.panels))
    lb_area = math.ceil(area / ((W - k) * (H - k)) - 1e-9)
    big = 0
    for (w, h), p in zip(sizes, job.panels):
        fits_both_halves = (w > W / 2 and h > H / 2) and (not p.rotate or (h > W / 2 and w > H / 2))
        if fits_both_halves:
            big += p.qty
    # wide panels (more than half the width, no turning) can only be stacked: a one-dimensional bound on heights
    wide = [h for (w, h), p in zip(sizes, job.panels) for _ in range(p.qty) if w > W / 2 and not p.rotate]
    lb_wide = math.ceil(sum(wide) / H - 1e-9) if wide else 0
    tall = [w for (w, h), p in zip(sizes, job.panels) for _ in range(p.qty) if h > H / 2 and not p.rotate]
    lb_tall = math.ceil(sum(tall) / W - 1e-9) if tall else 0
    return max(lb_area, big, lb_wide, lb_tall)


# ------------------------------------------------------------------------------------------------ solve
class Pool2D:
    def __init__(self, n):
        self.n, self.cols, self.stock, self.lays, self.seen = n, [], [], [], {}

    def add(self, s, lay):
        a = counts_of(lay, self.n)
        key = (int(s), tuple(int(x) for x in a))
        if key in self.seen or not a.any():
            return False
        self.seen[key] = len(self.cols)
        self.cols.append(a)
        self.stock.append(int(s))
        self.lays.append(lay)
        return True


def _cg(pool, dims, sizes, rot, d, cost, avail, deadline):
    S = len(dims)
    idx = [p for p in range(len(pool.cols)) if np.all(pool.cols[p] <= np.maximum(d, 0))]
    for s in range(S):                       # always at least one layout per needed panel type
        for i in np.nonzero(d > 0)[0]:
            one = np.zeros(len(d))
            one[i] = 1.0
            b = np.zeros(len(d), int)
            b[i] = int(d[i])
            v, lay = price(one, sizes, rot, b, dims[s])
            if v > 0:
                pool.add(s, lay)
                p = pool.seen.get((s, tuple(int(x) for x in counts_of(lay, len(d)))))
                if p is not None and p not in idx:
                    idx.append(p)
    master = Master(pool, d, cost, avail)
    for p in idx:
        master.add(p)
    while True:
        r = master.solve()
        if r is None:
            return None
        val, x, pi, sigma = r
        added = 0
        for s in range(S):
            v, lay = price(pi, sizes, rot, d.astype(int), dims[s])
            if cost[s] + sigma[s] - v < -1e-9 and pool.add(s, lay):
                master.add(len(pool.cols) - 1)
                added += 1
        full = np.concatenate([x, np.zeros(len(pool.cols) - len(x))])
        if added == 0:
            return val, full, True
        if time.time() > deadline:
            return val, full, False


def _dive(job, pool, dims, sizes, rot, d, cost, avail, deadline):
    rest, left, out = d.copy(), list(avail), []
    while rest.sum() > 0:
        r = _cg(pool, dims, sizes, rot, rest, cost, left, max(deadline, time.time() + 0.5))
        if r is None:
            return None
        _, x, _ = r
        fixed = False
        for p in np.argsort(-x):
            k = int(math.floor(x[p] + 1e-6))
            if k < 1:
                break
            nz = np.nonzero(pool.cols[p])[0]
            k = min(k, int(min(rest[i] // pool.cols[p][i] for i in nz)))
            s = pool.stock[p]
            if left[s] is not None:
                k = min(k, left[s])
            for _ in range(k):
                out.append((s, pool.lays[p]))
                rest = rest - pool.cols[p]
                if left[s] is not None:
                    left[s] -= 1
                fixed = True
        if not fixed:
            p = int(np.argmax(x))
            s = pool.stock[p]
            lay = cap_layout(pool.lays[p], np.maximum(rest, 0).astype(int), len(d))
            out.append((s, lay))
            rest = np.maximum(0, rest - counts_of(lay, len(d)))
            if left[s] is not None:
                left[s] -= 1
        rest = np.maximum(rest, 0)
        if time.time() > deadline and rest.sum() > 0:
            more = shelf_layouts(job, rest.astype(int), left)
            return None if more is None else out + more
    return out


def svc(job, pool, dims, sizes, rot, d, cost, avail, deadline, rng, target=0.0):
    """Sequential value correction: fill one sheet at a time as full as possible with the panels still needed
    (valued by area), then raise the value of panels that ended up on poorly filled sheets and repeat, so that
    awkward panels are placed early next time. Every layout found joins the pattern pool."""
    n = len(d)
    area = np.array([(w - int(round(job.kerf))) * (h - int(round(job.kerf))) for w, h in sizes], float)
    val = area.copy()
    best, best_cost = None, np.inf
    while time.time() < deadline:
        rest, left, out = d.astype(int).copy(), list(avail), []
        ok = True
        while rest.sum() > 0:
            choice = None
            for s_ in range(len(dims)):
                if left[s_] is not None and left[s_] <= 0:
                    continue
                v, lay = price(val * (1 + 0.02 * rng.random(n)), sizes, rot, rest, dims[s_])
                a = counts_of(lay, n)
                if a.sum() == 0:
                    continue
                score = float(np.dot(area, a)) / (dims[s_][0] * dims[s_][1]) / max(cost[s_], 1e-9)
                if choice is None or score > choice[0]:
                    choice = (score, s_, lay, a)
            if choice is None:
                ok = False
                break
            _, s_, lay, a = choice
            out.append((s_, lay))
            pool.add(s_, lay)
            rest -= a
            if left[s_] is not None:
                left[s_] -= 1
        if not ok:
            break
        c = float(sum(cost[s_] for s_, _ in out))
        if c < best_cost - 1e-9:
            best, best_cost = out, c
            if best_cost <= target + 1e-9:
                break
        fill = np.zeros(n)
        cnt = np.zeros(n)
        for s_, lay in out:
            a = counts_of(lay, n)
            f = float(np.dot(area, a)) / (dims[s_][0] * dims[s_][1])
            fill += a * f
            cnt += a
        avg = np.where(cnt > 0, fill / np.maximum(cnt, 1), 1.0)
        val = 0.5 * val + 0.5 * area / np.maximum(avg, 0.05)
    return best


def _cost(job, sheets):
    return float(sum(job.sheets[s].cost for s, _ in sheets))


def _trim(job, sheets):
    need = np.array([p.qty for p in job.panels], int)
    out = []
    for s, lay in sheets:
        a = counts_of(lay, len(need))
        lay2 = cap_layout(lay, np.minimum(a, need), len(need))
        need -= counts_of(lay2, len(need))
        if pieces_of(lay2):
            out.append((s, lay2))
    return out


def solve(job: SheetJob, seconds: float = 20.0, progress=None) -> SheetPlan:
    t0 = time.time()
    dims, sizes, k = job.scaled()
    n, S = len(sizes), len(dims)
    rot = [p.rotate for p in job.panels]
    d = np.array([p.qty for p in job.panels], float)
    cost = np.array([s.cost for s in job.sheets], float)
    avail = [s.available for s in job.sheets]
    bad = [job.panels[i].name for i in range(n)
           if not any((sizes[i][0] <= W and sizes[i][1] <= H) or (rot[i] and sizes[i][1] <= W and sizes[i][0] <= H)
                      for W, H in dims)]
    if bad:
        raise ValueError(f"larger than every sheet: {', '.join(bad[:3])}")
    pool = Pool2D(n)
    first = shelf_layouts(job)
    if first:
        for s, lay in first:
            pool.add(s, lay)
    for s in range(S):                                    # one panel type per sheet
        for i in range(n):
            one = np.zeros(n)
            one[i] = 1.0
            b = np.zeros(n, int)
            b[i] = int(d[i])
            v, lay = price(one, sizes, rot, b, dims[s])
            if v > 0:
                pool.add(s, lay)
    lb = lower_bound(job) * job.sheets[0].cost if lower_bound(job) else 0.0     # in money, like the plan's cost
    best = _trim(job, first) if first else None
    best_cost = _cost(job, best) if best else np.inf
    history = [(time.time() - t0, best_cost)]
    if best_cost <= lb + 1e-9:                            # the simple plan already needs no more than the bound
        return SheetPlan(best, best_cost, lb, "optimal", time.time() - t0, len(pool.cols), history)
    r = _cg(pool, dims, sizes, rot, d, cost, avail, t0 + seconds * 0.3)
    if r is None:
        raise ValueError("not enough sheets for all panels")

    def offer(b):
        nonlocal best, best_cost
        if b is None:
            return
        b = _trim(job, b)
        if counts_total(b, n) < d.sum():
            return
        c = _cost(job, b)
        if c < best_cost - 1e-9:
            best, best_cost = b, c
            history.append((time.time() - t0, c))
            if progress:
                progress(("plan", time.time() - t0, c))

    if best_cost > lb + 1e-9:
        offer(svc(job, pool, dims, sizes, rot, d, cost, avail, t0 + seconds * 0.55, np.random.default_rng(0), lb))
    if best_cost > lb + 1e-9:
        offer(_dive(job, pool, dims, sizes, rot, d, cost, avail, t0 + seconds * 0.75))
    if best_cost > lb + 1e-9 and time.time() - t0 < seconds:
        offer(_integer2d(pool, d, cost, avail, lb, t0, seconds, best))
    status = "optimal" if best_cost <= lb + 1e-9 else "best found"
    return SheetPlan(best, best_cost, lb, status, time.time() - t0, len(pool.cols), history)


def counts_total(sheets, n):
    return int(sum(counts_of(lay, n).sum() for _, lay in sheets))


def _integer2d(pool, d, cost, avail, lb, t0, seconds, hint):
    n, P = len(d), len(pool.cols)
    m = cp_model.CpModel()
    ub = int(d.max())
    x = [m.NewIntVar(0, ub, "") for _ in range(P)]
    for i in range(n):
        m.Add(sum(int(pool.cols[p][i]) * x[p] for p in range(P) if pool.cols[p][i]) >= int(d[i]))
    for s, a in enumerate(avail):
        if a is not None:
            m.Add(sum(x[p] for p in range(P) if pool.stock[p] == s) <= a)
    m.Minimize(sum(int(round(cost[pool.stock[p]] * 100)) * x[p] for p in range(P)))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = max(1.0, seconds - (time.time() - t0))
    sv.parameters.num_search_workers = 1
    st = sv.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out = []
    for p in range(P):
        out += [(pool.stock[p], pool.lays[p])] * int(sv.Value(x[p]))
    return out


# ------------------------------------------------------------------------------------------------ geometry
def rectangles(job: SheetJob, s: int, lay):
    """Pieces on one sheet in mm: [(panel, x, y, w, h, rotated)] with (0, 0) the sheet's top-left corner, plus the
    strip cut positions. Strips are stacked from the top, columns from the left, pieces in a column from the top of
    the column; saw kerf between all of them."""
    t, strips = lay
    k = job.kerf
    ki = int(round(k))
    out, cuts = [], []
    y = job.trim
    for hs, cols in strips:
        x = job.trim
        for wc, items in cols:
            yy = y
            for i, r in items:
                p = job.panels[i]
                if not t:
                    w, h = (p.height, p.width) if r else (p.width, p.height)
                    out.append((i, x, yy, w, h, r))
                    yy += h + k
                else:                                  # computed on the turned sheet: swap the axes back
                    wt, ht = (p.width, p.height) if r else (p.height, p.width)
                    out.append((i, yy, x, ht, wt, r))
                    yy += ht + k
            x += wc - ki + k
        cuts.append(y + hs - ki)
        y += hs - ki + k
    return out, cuts, (job.sheets[s].width, job.sheets[s].height)


def summary(job: SheetJob, sheets) -> dict:
    used = sum(job.sheets[s].width * job.sheets[s].height for s, _ in sheets)
    pieces = sum(job.panels[i].width * job.panels[i].height for _, lay in sheets for i, _ in pieces_of(lay))
    return {"sheets": len(sheets), "cost": _cost(job, sheets), "sheet_area": used, "panel_area": pieces,
            "waste_pct": 1 - pieces / used if used else 0.0, "yield_pct": pieces / used if used else 0.0}
