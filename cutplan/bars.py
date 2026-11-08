"""
Cutting pieces from bars (profiles, pipes, tubes, timber): the one-dimensional cutting stock problem.

Every bar is cut by a pattern (which pieces, in which order). The best mix of patterns is found by column generation
(Gilmore & Gomory 1961): a linear program chooses how often to use each known pattern, and a knapsack problem finds
the pattern that would lower the cost most at the current prices of the pieces. When no pattern helps any more, the
linear program's value is a lower bound: no plan can cost less. An integer plan is then built from the patterns
(CP-SAT) and, if needed, by rounding down and planning the rest again.

Saw kerf: every cut removes `kerf` mm. With n pieces from a bar, n - 1 cuts separate them (the last cut also frees the
offcut, which only has to be at least as long as the kerf to be cut off), so a bar of length L with trim t holds
pieces whose lengths plus (n - 1) kerfs fit in L - t. In integer units: capacity L - t + kerf, piece l + kerf.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import highspy
from ortools.sat.python import cp_model
from scipy.optimize import linprog


@dataclass
class Piece:
    name: str
    length: float
    qty: int


@dataclass
class Bar:
    name: str
    length: float
    cost: float = 1.0
    available: int | None = None          # None = as many as needed


@dataclass
class BarJob:
    pieces: list
    stock: list
    kerf: float = 0.0
    trim: float = 0.0                     # cut off each bar before use (both ends together)
    min_offcut: float = 0.0               # leftovers at least this long are kept for later jobs
    unit: float = 1.0                     # lengths are multiples of this (1 = whole mm)
    name: str = ""

    def scaled(self):
        """Integer capacities and piece sizes (kerf included)."""
        k = int(round(self.kerf / self.unit))
        caps = [int(math.floor((b.length - self.trim) / self.unit + 1e-9)) + k for b in self.stock]
        sizes = [int(math.ceil(p.length / self.unit - 1e-9)) + k for p in self.pieces]
        return caps, sizes


@dataclass
class BarPlan:
    bars: list                             # (stock index, [piece index, ...]) one entry per bar cut
    cost: float
    lower_bound: float                     # no plan can cost less (column generation bound, rounded up for cost 1)
    status: str                            # "optimal" (cost = bound) or "best found"
    seconds: float
    columns: int = 0
    history: list = field(default_factory=list)

    def patterns(self):
        """Identical bars grouped: [(stock index, pieces tuple, count)], most used first."""
        groups = {}
        for s, ps in self.bars:
            key = (s, tuple(sorted(ps)))
            groups[key] = groups.get(key, 0) + 1
        return sorted(((s, ps, n) for (s, ps), n in groups.items()), key=lambda g: (-g[2], g[0], g[1]))


# ------------------------------------------------------------------------------------------------ knapsack
def knapsack(values, weights, bounds, cap, more=0):
    """Bounded knapsack: max sum v_i a_i, sum w_i a_i <= cap, 0 <= a_i <= b_i (integer weights).
    Dynamic programming over the capacity, items split into powers of two. Returns (value, counts); with more > 0 a
    list of up to more + 1 (value, counts), best first."""
    values = np.asarray(values, float)
    n = len(values)
    counts = np.zeros(n, dtype=int)
    if cap <= 0 or n == 0:
        return (0.0, counts) if more <= 0 else [(0.0, counts)]
    chunks = []
    for i in range(n):
        if values[i] <= 1e-12 or weights[i] > cap or bounds[i] <= 0:
            continue
        b = int(min(bounds[i], cap // weights[i]))
        k = 1
        while b > 0:
            m = min(k, b)
            chunks.append((i, m))
            b -= m
            k *= 2
    dp = np.zeros(cap + 1)
    take = []
    for i, m in chunks:
        w, v = int(weights[i] * m), values[i] * m
        if w > cap:
            take.append(None)
            continue
        cand = dp[:cap + 1 - w] + v            # computed from the table before this chunk: each chunk used once
        better = cand > dp[w:] + 1e-12
        np.copyto(dp[w:], cand, where=better)
        take.append(better)
    def back(c):
        a = np.zeros(n, dtype=int)
        for (i, m), t in zip(reversed(chunks), reversed(take)):
            w = int(weights[i] * m)
            if t is not None and c >= w and t[c - w]:
                a[i] += m
                c -= w
        return a

    c = int(np.argmax(dp))
    if more <= 0:
        return float(dp[c]), back(c)
    # also the best patterns for a few shorter capacities (the table already holds them): more columns per table
    out, seen = [], set()
    for c in np.argsort(-dp, kind="stable")[: 50 * (more + 1)]:
        if dp[c] <= 1e-12:
            break
        a = back(int(c))
        key = tuple(a)
        if key not in seen:
            seen.add(key)
            out.append((float(dp[c]), a))
            if len(out) > more:
                break
    return out or [(0.0, counts)]


# ------------------------------------------------------------------------------------------------ simple plans
def ffd(job: BarJob, demand=None, stock_left=None, decreasing=True):
    """First fit decreasing, what a careful cutter does by hand: longest piece first, into the first bar it fits in, a
    new bar (the cheapest per metre that is still in stock) when none has room. With decreasing=False the pieces are
    cut in the order of the list (first fit). Returns [(stock, [pieces])] or None."""
    caps, sizes = job.scaled()
    demand = [p.qty for p in job.pieces] if demand is None else list(demand)
    left = [b.available for b in job.stock] if stock_left is None else list(stock_left)
    order = [i for i in range(len(sizes)) for _ in range(demand[i])]
    if decreasing:
        order.sort(key=lambda i: -sizes[i])
    bars, free = [], []
    for i in order:
        for k, (s, ps) in enumerate(bars):
            if free[k] >= sizes[i]:
                ps.append(i)
                free[k] -= sizes[i]
                break
        else:
            cands = [s for s in range(len(caps)) if caps[s] >= sizes[i] and (left[s] is None or left[s] > 0)]
            if not cands:
                return None
            s = min(cands, key=lambda s: (job.stock[s].cost / max(caps[s], 1), -caps[s]))
            if left[s] is not None:
                left[s] -= 1
            bars.append((s, [i]))
            free.append(caps[s] - sizes[i])
    return bars


def _best_stock_for(job, caps, pieces_size, left):
    """Cheapest stock that holds this many units and is still available."""
    c = [s for s in range(len(caps)) if caps[s] >= pieces_size and (left[s] is None or left[s] > 0)]
    return min(c, key=lambda s: job.stock[s].cost) if c else None


def shrink(job: BarJob, bars):
    """Move bars to the cheapest stock their pieces fit in (e.g. a stored offcut instead of a new bar), never using
    more of a stock than is available: a small transportation problem (integral by total unimodularity)."""
    caps, sizes = job.scaled()
    S = len(caps)
    if S == 1 or not bars:
        return bars
    need = [sum(sizes[i] for i in ps) for _, ps in bars]
    groups = {}
    for b, nd in enumerate(need):
        groups.setdefault(nd, []).append(b)
    keys = sorted(groups)
    var = [(g, t) for g, nd in enumerate(keys) for t in range(S) if caps[t] >= nd]
    c = [job.stock[t].cost for _, t in var]
    A_eq = np.zeros((len(keys), len(var)))
    for v, (g, _t) in enumerate(var):
        A_eq[g, v] = 1
    b_eq = [len(groups[k]) for k in keys]
    lim = [t for t in range(S) if job.stock[t].available is not None]
    A_ub = np.zeros((len(lim), len(var)))
    for r, t in enumerate(lim):
        for v, (_g, t2) in enumerate(var):
            if t2 == t:
                A_ub[r, v] = 1
    b_ub = [job.stock[t].available for t in lim]
    res = linprog(c, A_ub=A_ub if lim else None, b_ub=b_ub if lim else None, A_eq=A_eq, b_eq=b_eq,
                  bounds=(0, None), method="highs-ds")
    if res.status != 0:
        return bars
    y = np.round(res.x).astype(int)
    if np.abs(res.x - y).max() > 1e-6:
        return bars
    out = []
    todo = {g: list(groups[k]) for g, k in enumerate(keys)}
    for v, (g, t) in enumerate(var):
        for _ in range(y[v]):
            out.append((t, bars[todo[g].pop()][1]))
    return out


def round_bound(bound, costs):
    """A plan's cost is a sum of stock costs: round the bound up to the next multiple of their greatest common
    divisor (costs in cents)."""
    cents = [int(round(c * 100)) for c in costs]
    if any(abs(c * 100 - k) > 1e-6 for c, k in zip(costs, cents)):
        return bound
    g = 0
    for k in cents:
        g = math.gcd(g, k)
    if g == 0:
        return bound
    return math.ceil(bound * 100 / g - 1e-6 * max(1.0, bound)) * g / 100


# ------------------------------------------------------------------------------------------------ column generation
class Pool:
    """The patterns found so far (columns), shared by the bound, the dives and the final integer program."""

    def __init__(self, n):
        self.n, self.cols, self.stock, self.seen = n, [], [], {}

    def add(self, s, a):
        a = np.asarray(a, int)
        key = (int(s), tuple(int(x) for x in a))
        if key in self.seen or not a.any():
            return False
        self.seen[key] = len(self.cols)
        self.cols.append(a)
        self.stock.append(int(s))
        return True

    def index(self, s, a):
        """Index of this column, adding it if new."""
        a = np.asarray(a, int)
        self.add(s, a)
        return self.seen.get((int(s), tuple(int(x) for x in a)))


class Master:
    """The restricted master linear program: use each pattern x_p >= 0 times, at least the demand of every piece,
    at most the stock available, least cost. Kept in HiGHS between rounds, so every re-solve starts from the last
    basis (warm start) instead of from scratch."""

    def __init__(self, pool, d, cost, avail):
        self.pool, self.cost, self.n, self.S = pool, cost, len(d), len(cost)
        self.h = highspy.Highs()
        self.h.setOptionValue("output_flag", False)
        inf = highspy.kHighsInf
        none_i, none_v = np.array([], dtype=np.int32), np.array([], dtype=float)
        for i in range(self.n):
            self.h.addRow(float(d[i]), inf, 0, none_i, none_v)
        self.row = {}
        for s in range(self.S):
            if avail[s] is not None:
                self.row[s] = self.h.getNumRow()
                self.h.addRow(-inf, float(avail[s]), 0, none_i, none_v)
        self.idx = []

    def add(self, p):
        a = self.pool.cols[p]
        nz = np.nonzero(a)[0]
        rows, vals = list(nz), [float(v) for v in a[nz]]
        s = self.pool.stock[p]
        if s in self.row:
            rows.append(self.row[s])
            vals.append(1.0)
        self.h.addCol(float(self.cost[s]), 0.0, highspy.kHighsInf, len(rows), np.array(rows, dtype=np.int32),
                      np.array(vals, dtype=float))
        self.idx.append(p)

    def solve(self):
        """(value, x over the pool, pi, sigma) or None if the stock cannot cover the demand. pi = price of each
        piece, sigma = extra price of each limited stock (reduced cost of a pattern a on stock s:
        cost_s + sigma_s - pi.a)."""
        self.h.run()
        if self.h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
            return None
        sol = self.h.getSolution()
        du = np.asarray(sol.row_dual, dtype=float)
        sigma = np.zeros(self.S)
        for s, r in self.row.items():
            sigma[s] = max(0.0, -du[r])
        x = np.zeros(len(self.pool.cols))
        x[self.idx] = np.asarray(sol.col_value, dtype=float)
        return float(self.h.getInfo().objective_function_value), x, np.maximum(du[:self.n], 0.0), sigma


def column_generation(pool, caps, sizes, d, cost, avail, deadline, many=3):
    """Gilmore-Gomory column generation for demand d. Returns (lp value, x over pool, bound, converged)."""
    S = len(caps)
    idx = [p for p in range(len(pool.cols)) if np.all(pool.cols[p] <= np.maximum(d, 0))]
    for s in range(S):                       # always at least one pattern per needed piece type
        for i in np.nonzero(d > 0)[0]:
            if sizes[i] <= caps[s]:
                a = np.zeros(len(d), int)
                a[i] = min(int(d[i]), caps[s] // sizes[i])
                p = pool.index(s, a)
                if p is not None and p not in idx:
                    idx.append(p)
    bound = 0.0
    master = Master(pool, d, cost, avail)
    for p in idx:
        master.add(p)
    while True:
        r = master.solve()
        if r is None:
            return None
        val, x, pi, sigma = r
        added, theta = 0, 0.0
        for s in range(S):
            if caps[s] > 20000:              # long bars in fine units: one table, several patterns from it
                found = knapsack(pi, sizes, d.astype(int), caps[s], more=4)
                v = found[0][0]
                theta = max(theta, v / cost[s] if cost[s] > 0 else np.inf)
                for v2, a2 in found:
                    if cost[s] + sigma[s] - v2 < -1e-9 and pool.add(s, a2):
                        master.add(len(pool.cols) - 1)
                        added += 1
                continue
            v, a = knapsack(pi, sizes, d.astype(int), caps[s])
            theta = max(theta, v / cost[s] if cost[s] > 0 else np.inf)
            if cost[s] + sigma[s] - v < -1e-9 and pool.add(s, a):
                master.add(len(pool.cols) - 1)
                added += 1
                # a few more good patterns around this one: drop each piece type in turn and refill
                for i in np.nonzero(a)[0][:many]:
                    b = d.astype(int).copy()
                    b[i] = max(0, int(a[i]) - 1)
                    v2, a2 = knapsack(pi, sizes, b, caps[s])
                    if cost[s] + sigma[s] - v2 < -1e-9 and pool.add(s, a2):
                        master.add(len(pool.cols) - 1)
        if all(a is None for a in avail) and theta > 0:
            bound = max(bound, val / max(theta, 1.0))         # Farley bound: valid at every iteration
        x = np.concatenate([x, np.zeros(len(pool.cols) - len(x))])
        if added == 0:
            return val, x, val, True
        if time.time() > deadline:
            return val, x, bound, False


def _bars_of(pool, p, n):
    return (pool.stock[p], [i for i in range(n) for _ in range(int(pool.cols[p][i]))])


def dive(job, pool, caps, sizes, d, cost, avail, deadline, rng=None):
    """Diving: plan the integer part of the LP, fix the most used pattern if nothing is integer, re-solve the rest
    with column generation, repeat. Finds the optimum on most instances whose LP bound is tight."""
    n = len(d)
    rest, left, bars = d.copy(), list(avail), []
    while rest.sum() > 0:
        r = column_generation(pool, caps, sizes, rest, cost, left, max(deadline, time.time() + 0.5), many=1)
        if r is None:
            return None
        _, x, _, _ = r
        fixed = False
        for p in np.argsort(-x):
            k = int(math.floor(x[p] + 1e-6))
            if k < 1:
                break
            if rng is not None and rng.random() < 0.3:      # randomised dive: leave some for later
                continue
            k = min(k, int(min(rest[i] // pool.cols[p][i] for i in np.nonzero(pool.cols[p])[0])))
            s = pool.stock[p]
            if left[s] is not None:
                k = min(k, left[s])
            for _ in range(k):
                bars.append(_bars_of(pool, p, n))
                rest = rest - pool.cols[p]
                if left[s] is not None:
                    left[s] -= 1
                fixed = True
        if not fixed:
            if rng is None:
                p = int(np.argmax(x))
            else:                                           # randomised dive: draw in proportion to use
                used = np.nonzero(x > 1e-6)[0]
                w = x[used] ** 2
                p = int(rng.choice(used, p=w / w.sum()))
            s = pool.stock[p]
            bars.append(_bars_of(pool, p, n))
            rest = np.maximum(0, rest - pool.cols[p])
            if left[s] is not None:
                left[s] -= 1
        rest = np.maximum(rest, 0)
        if time.time() > deadline and rest.sum() > 0:
            more = ffd(job, rest.astype(int), left)
            return None if more is None else bars + more
    return bars


def solve(job: BarJob, seconds: float = 20.0, progress=None) -> BarPlan:
    t0 = time.time()
    caps, sizes = job.scaled()
    n, S = len(sizes), len(caps)
    d = np.array([p.qty for p in job.pieces], dtype=float)
    cost = np.array([b.cost for b in job.stock], float)
    avail = [b.available for b in job.stock]
    bad = [job.pieces[i].name for i in range(n) if all(sizes[i] > c for c in caps)]
    if bad:
        raise ValueError(f"longer than every stock length: {', '.join(bad[:3])}")
    pool = Pool(n)
    for s in range(S):                       # one piece type per bar, as many as fit
        for i in range(n):
            if sizes[i] <= caps[s]:
                a = np.zeros(n, int)
                a[i] = min(int(d[i]), caps[s] // sizes[i])
                pool.add(s, a)
    first = ffd(job)
    if first:
        for s, ps in first:
            pool.add(s, np.bincount(ps, minlength=n))
    history = []
    r = column_generation(pool, caps, sizes, d, cost, avail, t0 + seconds * 0.35)
    if r is None:
        raise ValueError("not enough stock for all pieces")
    lp_val, x_lp, bound, converged = r
    lb = max(round_bound(bound, list(cost)), 0)
    if progress:
        progress(("bound", time.time() - t0, lb))
    best = first
    best_cost = _cost(job, first) if first else np.inf
    history.append((time.time() - t0, best_cost))

    def offer(b):
        nonlocal best, best_cost
        if b is None:
            return
        b = shrink(job, _trim_extra(job, b))
        c = _cost(job, b)
        if c < best_cost - 1e-9:
            best, best_cost = b, c
            history.append((time.time() - t0, c))
            if progress:
                progress(("plan", time.time() - t0, c))

    offer(_residual(job, pool.cols, pool.stock, x_lp, d, avail))
    if best_cost > lb + 1e-9:
        offer(dive(job, pool, caps, sizes, d, cost, avail, t0 + seconds * 0.6))
    rng = np.random.default_rng(0)
    while best_cost > lb + 1e-9 and time.time() - t0 < seconds * 0.75:
        offer(dive(job, pool, caps, sizes, d, cost, avail, t0 + seconds * 0.75, rng))
    while best_cost > lb + 1e-9 and time.time() - t0 < seconds - 0.5:
        offer(_integer(job, pool.cols, pool.stock, np.zeros(len(pool.cols)), cost, d, avail, lb, t0, seconds,
                       hint=best))
        # the pattern pool cannot do better: grow it with more randomised dives while time is left
        while best_cost > lb + 1e-9 and time.time() - t0 < seconds - 0.5:
            before = len(pool.cols)
            offer(dive(job, pool, caps, sizes, d, cost, avail, min(t0 + seconds, time.time() + seconds * 0.1), rng))
            if len(pool.cols) > before + 50:
                break
    status = "optimal" if best_cost <= lb + 1e-9 else "best found"
    return BarPlan(best, best_cost, lb, status, time.time() - t0, len(pool.cols), history)


def _cost(job, bars):
    return float(sum(job.stock[s].cost for s, _ in bars))


def _integer(job, cols, col_stock, x_lp, cost, d, avail, lb, t0, seconds, hint=None):
    n, P = len(d), len(cols)
    m = cp_model.CpModel()
    ub = int(d.max()) if len(d) else 0
    x = [m.NewIntVar(0, ub, "") for _ in range(P)]
    for i in range(n):
        m.Add(sum(int(cols[p][i]) * x[p] for p in range(P) if cols[p][i]) >= int(d[i]))
    for s, a in enumerate(avail):
        if a is not None:
            m.Add(sum(x[p] for p in range(P) if col_stock[p] == s) <= a)
    scale = 1 if all(abs(c - round(c)) < 1e-9 for c in cost) else 100
    m.Minimize(sum(int(round(cost[col_stock[p]] * scale)) * x[p] for p in range(P)))
    if hint:
        use = {}
        index = {(col_stock[p], tuple(int(v) for v in cols[p])): p for p in range(P)}
        for s_, ps in hint:
            key = (s_, tuple(int(v) for v in np.bincount(ps, minlength=n)))
            if key in index:
                use[index[key]] = use.get(index[key], 0) + 1
        for p in range(P):
            m.AddHint(x[p], min(ub, use.get(p, 0)))
    if scale == 1:
        m.Add(sum(int(round(cost[col_stock[p]])) * x[p] for p in range(P)) >= int(lb))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = max(1.0, seconds - (time.time() - t0))
    sv.parameters.num_search_workers = 1

    class Stop(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            if scale == 1 and self.ObjectiveValue() <= lb + 1e-9:
                self.StopSearch()

    cb = Stop()
    st = sv.Solve(m, cb)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    bars = []
    for p in range(P):
        for _ in range(sv.Value(x[p])):
            bars.append((col_stock[p], [i for i in range(n) for _ in range(int(cols[p][i]))]))
    return bars


def _residual(job, cols, col_stock, x_lp, d, avail):
    """Round the linear program down, plan the remaining pieces by first fit decreasing."""
    n = len(d)
    bars = []
    left = [a for a in avail]
    got = np.zeros(n, int)
    for p, v in enumerate(x_lp):
        k = int(math.floor(v + 1e-9))
        s = col_stock[p]
        if left[s] is not None:
            k = min(k, left[s])
            left[s] -= k
        for _ in range(k):
            bars.append((s, [i for i in range(n) for _ in range(int(cols[p][i]))]))
            got += cols[p]
    rest = np.maximum(0, d.astype(int) - got)
    more = ffd(job, rest, left) if rest.sum() else []
    if more is None:
        return None
    return bars + more


def _trim_extra(job, bars):
    """Pieces cut beyond what was ordered are left in the offcut instead."""
    need = [p.qty for p in job.pieces]
    out = []
    for s, ps in bars:
        keep = []
        for i in ps:
            if need[i] > 0:
                keep.append(i)
                need[i] -= 1
        if keep:
            out.append((s, keep))
    return out


# ------------------------------------------------------------------------------------------------ reading the plan
def bar_layout(job: BarJob, s: int, pieces) -> dict:
    """Positions along one bar (mm): trim, pieces longest first, kerfs, and the offcut."""
    order = sorted(pieces, key=lambda i: -job.pieces[i].length)
    pos = job.trim / 2
    segs = []
    for k, i in enumerate(order):
        if k:
            pos += job.kerf
        segs.append((i, pos, job.pieces[i].length))
        pos += job.pieces[i].length
    L = job.stock[s].length
    end = L - job.trim / 2
    off = end - pos - (job.kerf if pos < end - 1e-9 else 0)
    return {"segments": segs, "offcut": max(0.0, off), "offcut_start": pos + (job.kerf if off > 0 else 0),
            "length": L}


def summary(job: BarJob, bars) -> dict:
    used = sum(job.stock[s].length for s, _ in bars)
    pieces = sum(job.pieces[i].length for _, ps in bars for i in ps)
    offcuts = [bar_layout(job, s, ps)["offcut"] for s, ps in bars]
    kept = [o for o in offcuts if job.min_offcut > 0 and o >= job.min_offcut]
    scrap = used - pieces - sum(kept)
    return {"bars": len(bars), "cost": _cost(job, bars), "stock_length": used, "pieces_length": pieces,
            "kept_offcuts": kept, "kept_length": float(sum(kept)), "scrap": scrap,
            "waste_pct": scrap / used if used else 0.0, "yield_pct": pieces / used if used else 0.0}
