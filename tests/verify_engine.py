"""
Engine checks: the knapsack and the bar planner against brute force, saw kerf and trim, stock limits and offcuts, the
sheet planner on cases with a known answer, the independent checker rule by rule, published optima, the Excel job
format, the reports and the demo jobs.

    python tests/verify_engine.py            # about 4 minutes
    python tests/verify_engine.py --quick    # the checks the mutation test needs (no benchmark files)
"""

from __future__ import annotations

import io
import itertools
import os
import re
import sys

ROOT = os.environ.get("CP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cutplan import bars as B  # noqa: E402
from cutplan import data, jobs, report  # noqa: E402
from cutplan import sheets as S  # noqa: E402
from cutplan.checks import check_bars, check_sheets, guillotine  # noqa: E402

QUICK = "--quick" in sys.argv
passed, failed = 0, []


def ok(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


# ------------------------------------------------------------------------------------------------ brute force
def brute_bars(job):
    """Least cost by trying every way to split the pieces into bars (stock unlimited). A set of pieces fits a bar
    when their lengths plus a kerf between neighbours fit in the bar minus the trim, computed here from scratch."""
    items = [i for i, p in enumerate(job.pieces) for _ in range(p.qty)]
    n = len(items)
    INF = float("inf")
    sub = [INF] * (1 << n)
    for mask in range(1, 1 << n):
        ps = [items[k] for k in range(n) if mask >> k & 1]
        need = sum(job.pieces[i].length for i in ps) + job.kerf * (len(ps) - 1)
        fits = [b.cost for b in job.stock if need <= b.length - job.trim + 1e-9]
        sub[mask] = min(fits) if fits else INF
    f = [INF] * (1 << n)
    f[0] = 0.0
    for mask in range(1, 1 << n):
        low = mask & -mask
        s = mask
        while s:
            if s & low and sub[s] < INF:
                f[mask] = min(f[mask], sub[s] + f[mask ^ s])
            s = (s - 1) & mask
    return f[(1 << n) - 1]


def tiny_bars(seed, stocks=1):
    rng = np.random.default_rng(seed)
    L = int(rng.integers(80, 200))
    pieces = []
    total = 0
    while total < int(rng.integers(5, 10)):
        q = int(rng.integers(1, 3))
        pieces.append(B.Piece(f"p{len(pieces)}", float(rng.integers(10, L - 20)), q))
        total += q
    stock = [B.Bar("long", float(L), 1.0 if stocks == 1 else float(rng.integers(5, 9)))]
    if stocks > 1:
        stock.append(B.Bar("short", float(int(L * rng.uniform(0.5, 0.8))), float(rng.integers(2, 5))))
    return B.BarJob(pieces, stock, kerf=float(rng.integers(0, 4)), trim=float(rng.integers(0, 6)), name=f"t{seed}")


print("Knapsack")
rng = np.random.default_rng(5)
bad = 0
for t in range(300):
    n = int(rng.integers(1, 5))
    w = rng.integers(1, 12, n)
    b = rng.integers(0, 4, n)
    v = rng.random(n) * (rng.random(n) > 0.2)
    cap = int(rng.integers(0, 30))
    best = max(float(np.dot(a, v)) for a in itertools.product(*[range(x + 1) for x in b]) if np.dot(a, w) <= cap)
    val, cnt = B.knapsack(v, w, b, cap)
    if abs(val - best) > 1e-9 or np.dot(cnt, w) > cap or np.any(cnt > b) or abs(np.dot(cnt, v) - val) > 1e-9:
        bad += 1
ok("bounded knapsack = brute force on 300 random cases", bad == 0, f"{bad} wrong")
many = B.knapsack(np.array([3.0, 2.0, 1.5]), [5, 4, 3], [2, 2, 2], 13, more=3)
ok("several patterns from one table: valid, best first",
   all(np.dot(a, [5, 4, 3]) <= 13 and np.all(a <= 2) for _, a in many) and
   all(many[k][0] >= many[k + 1][0] for k in range(len(many) - 1)) and abs(many[0][0] - 7.5) < 1e-9, str(many))

print("Bars against brute force")
wrong, invalid, lbad = [], 0, 0
for seed in range(20):
    job = tiny_bars(seed)
    opt = brute_bars(job)
    plan = B.solve(job, seconds=3)
    if abs(plan.cost - opt) > 1e-9:
        wrong.append((seed, plan.cost, opt))
    invalid += bool(check_bars(job, plan.bars))
    lbad += plan.lower_bound > opt + 1e-9
ok("20 random bar jobs: the plan is optimal (brute force)", not wrong, str(wrong[:3]))
ok("20 random bar jobs: every plan passes the checker", invalid == 0)
ok("20 random bar jobs: the lower bound never exceeds the optimum", lbad == 0)
wrong = []
for seed in range(100, 110):
    job = tiny_bars(seed, stocks=2)
    opt = brute_bars(job)
    plan = B.solve(job, seconds=3)
    if abs(plan.cost - opt) > 1e-9 or check_bars(job, plan.bars) or plan.lower_bound > opt + 1e-9:
        wrong.append((seed, plan.cost, opt, plan.lower_bound))
ok("10 jobs with two stock lengths and prices: least cost (brute force)", not wrong, str(wrong[:3]))

print("Saw kerf, trim, stock limits")
j = B.BarJob([B.Piece("a", 495, 2)], [B.Bar("1 m", 1000)], kerf=5, trim=10)
p = B.solve(j, seconds=2)
ok("495 + kerf 5 + 495 does not fit 1000 - trim 10: two bars", p.cost == 2 and not check_bars(j, p.bars))
j = B.BarJob([B.Piece("a", 490, 2)], [B.Bar("1 m", 1000)], kerf=5, trim=10)
p = B.solve(j, seconds=2)
ok("490 + kerf 5 + 490 fits 1000 - trim 10: one bar", p.cost == 1 and not check_bars(j, p.bars))
lay = B.bar_layout(B.BarJob([B.Piece("a", 400, 2)], [B.Bar("1 m", 1000)], kerf=5, trim=10), 0, [0, 0])
ends = [(x, x + l) for _, x, l in lay["segments"]]
ok("bar layout: half the trim at the start, a kerf between pieces and before the offcut",
   abs(ends[0][0] - 5) < 1e-9 and abs(ends[1][0] - ends[0][1] - 5) < 1e-9 and
   abs(lay["offcut"] - (1000 - 10 - 800 - 5 - 5)) < 1e-9, str(lay))
rack = B.BarJob([B.Piece("a", 1400, 6), B.Piece("b", 900, 5)],
                [B.Bar("6 m", 6000, 40), B.Bar("rack 2500", 2500, 0, 2), B.Bar("rack 1500", 1500, 0, 1)],
                kerf=4, trim=20)
p = B.solve(rack, seconds=3)
used = pd.Series([s for s, _ in p.bars]).value_counts()
ok("offcuts in the rack: used before new bars, never more than there are",
   not check_bars(rack, p.bars) and used.get(1, 0) == 2 and used.get(2, 0) == 1, str(dict(used)))
bars_in = [(0, [0]), (0, [0]), (0, [0]), (1, [1])]
sh = B.shrink(rack, bars_in)
cnt = pd.Series([s for s, _ in sh]).value_counts()
ok("moving bars to cheaper offcuts respects how many there are",
   cnt.get(1, 0) <= 2 and cnt.get(2, 0) <= 1 and not check_bars(
       B.BarJob([B.Piece("a", 1400, 3), B.Piece("b", 900, 1)], rack.stock, kerf=4, trim=20), sh), str(sh))
try:
    B.solve(B.BarJob([B.Piece("long", 7000, 1)], [B.Bar("6 m", 6000)]), seconds=1)
    ok("a piece longer than every bar is reported", False)
except ValueError as exc:
    ok("a piece longer than every bar is reported", "longer" in str(exc))
try:
    B.solve(B.BarJob([B.Piece("a", 3000, 5)], [B.Bar("6 m", 6000, 1, 2)]), seconds=1)
    ok("not enough stock is reported", False)
except ValueError as exc:
    ok("not enough stock is reported", "not enough" in str(exc))
ok("bounds round up to what a plan can cost",
   B.round_bound(11.2, [1.0]) == 12 and B.round_bound(396.0, [35.0]) == 420 and B.round_bound(7.0, [0.0, 42.0]) == 42
   and abs(B.round_bound(10.0000001, [1.0]) - 10) < 1e-9 and B.round_bound(5.5, [1.25]) == 6.25)
lst = B.ffd(rack, decreasing=False)
ok("by-hand plans (longest first, as listed) are valid", not check_bars(rack, B.ffd(rack)) and not check_bars(rack, lst))
ok("exactly the pieces ordered are cut (no extra pieces)",
   all(not check_bars(tiny_bars(s), B.solve(tiny_bars(s), seconds=2).bars) for s in (40, 41, 42)))

print("Sheets")
q = S.SheetJob([S.Panel("q", 500, 300, 4)], [S.Sheet("s", 1000, 600)])
p = S.solve(q, seconds=3)
ok("four quarter panels: one sheet", p.cost == 1 and not check_sheets(q, p.sheets, S.rectangles))
q = S.SheetJob([S.Panel("q", 498, 298, 4)], [S.Sheet("s", 1000, 600)], kerf=4)
p = S.solve(q, seconds=3)
ok("four panels with a 4 mm kerf between them: one sheet", p.cost == 1 and not check_sheets(q, p.sheets, S.rectangles))
q = S.SheetJob([S.Panel("q", 499, 298, 4)], [S.Sheet("s", 1000, 600)], kerf=4)
p = S.solve(q, seconds=3)
ok("one millimetre more: three fit a sheet (one turned), two sheets",
   p.cost == 2 and not check_sheets(q, p.sheets, S.rectangles) and S.lower_bound(q) <= 2)
g = S.SheetJob([S.Panel("long", 2000, 300, 4, rotate=False)], [S.Sheet("s", 2440, 1220)], kerf=4)
p = S.solve(g, seconds=3)
ok("grain along the length: four 2000 x 300 panels share one sheet, none turned",
   p.cost == 1 and not check_sheets(g, p.sheets, S.rectangles) and
   not any(r for _, lay in p.sheets for _, r in S.pieces_of(lay)))
try:
    S.solve(S.SheetJob([S.Panel("x", 300, 2000, 1, rotate=False)], [S.Sheet("s", 2440, 1220)]), seconds=1)
    ok("a grain panel that only fits turned is reported", False)
except ValueError as exc:
    ok("a grain panel that only fits turned is reported", "larger" in str(exc))
p = S.solve(S.SheetJob([S.Panel("x", 300, 2000, 1)], [S.Sheet("s", 2440, 1220)]), seconds=1)
ok("without grain the same panel turns and fits", p.cost == 1)
bad, worse = [], []
for seed in range(8):
    r = np.random.default_rng(seed)
    W, H = 2440, 1220
    panels = [S.Panel(f"p{k}", float(r.integers(150, 1200)), float(r.integers(100, 700)), int(r.integers(1, 6)),
                      rotate=bool(r.random() < 0.6)) for k in range(int(r.integers(3, 8)))]
    sj = S.SheetJob(panels, [S.Sheet("s", W, H, 50.0)], kerf=float(r.integers(0, 5)), trim=float(r.integers(0, 15)))
    p = S.solve(sj, seconds=3)
    pr = check_sheets(sj, p.sheets, S.rectangles)
    hand = S.shelf_layouts(sj)
    if pr or p.lower_bound > p.cost + 1e-9:
        bad.append((seed, pr[:2]))
    if p.cost > S._cost(sj, hand) + 1e-9:
        worse.append(seed)
ok("8 random sheet jobs (grain, kerf, trim): every plan passes the checker", not bad, str(bad[:2]))
ok("8 random sheet jobs: never more sheets than by hand", not worse, str(worse))
two = S.SheetJob([S.Panel("a", 800, 600, 6)], [S.Sheet("small", 1300, 1220, 30, 1), S.Sheet("big", 2440, 1220, 50)],
                 kerf=4)
p = S.solve(two, seconds=3)
ok("two sheet sizes, one limited: the limit holds", not check_sheets(two, p.sheets, S.rectangles))

print("Checker")
cj = B.BarJob([B.Piece("a", 500, 2)], [B.Bar("1 m", 1000, 1, 1)], kerf=4)
ok("bars: too long with the kerf is caught", any("need" in x for x in check_bars(cj, [(0, [0, 0])])))
ok("bars: missing and extra pieces are caught", bool(check_bars(cj, [(0, [0])])) and
   bool(check_bars(cj, [(0, [0]), (0, [0]), (0, [0])])))
ok("bars: more stock than available is caught", any("available" in x for x in check_bars(
    B.BarJob([B.Piece("a", 400, 2)], [B.Bar("1 m", 1000, 1, 1)]), [(0, [0]), (0, [0])])))


def fake(rects):
    return lambda job, s, lay: (rects, [], (job.sheets[s].width, job.sheets[s].height))


sj = S.SheetJob([S.Panel("a", 400, 300, 2, rotate=False)], [S.Sheet("s", 1000, 600)], kerf=4)
good = [(0, 0, 0, 400, 300, False), (0, 404, 0, 400, 300, False)]
ok("sheets: a correct layout passes", not check_sheets(sj, [(0, None)], fake(good)))
ok("sheets: overlap is caught", bool(check_sheets(sj, [(0, None)], fake([(0, 0, 0, 400, 300, False),
                                                                          (0, 300, 0, 400, 300, False)]))))
ok("sheets: panels closer than the kerf are caught", bool(check_sheets(sj, [(0, None)], fake(
    [(0, 0, 0, 400, 300, False), (0, 402, 0, 400, 300, False)]))))
ok("sheets: a panel outside the sheet is caught", bool(check_sheets(sj, [(0, None)], fake(
    [(0, 0, 0, 400, 300, False), (0, 700, 0, 400, 300, False)]))))
ok("sheets: a panel turned against the grain is caught", any("grain" in x for x in check_sheets(sj, [(0, None)], fake(
    [(0, 0, 0, 400, 300, False), (0, 404, 0, 300, 400, True)]))))
ok("sheets: a panel of the wrong size is caught", any("size" in x for x in check_sheets(sj, [(0, None)], fake(
    [(0, 0, 0, 400, 300, False), (0, 404, 0, 390, 300, False)]))))
ok("sheets: a missing panel is caught", any("ordered" in x for x in check_sheets(sj, [(0, None)], fake(good[:1]))))
pin = [(0, 0, 2, 1), (2, 0, 1, 2), (1, 2, 2, 1), (0, 1, 1, 2), (1, 1, 1, 1)]
ok("guillotine test: the pinwheel cannot be cut edge to edge", not guillotine(pin, 0, 0, 3, 3))
ok("guillotine test: strips of pieces can", guillotine([(0, 0, 2, 1), (2, 0, 1, 1), (0, 1, 3, 2)], 0, 0, 3, 3))
pj = S.SheetJob([S.Panel("x", 2, 1, 2, rotate=False), S.Panel("y", 1, 2, 2, rotate=False),
                 S.Panel("z", 1, 1, 1, rotate=False)], [S.Sheet("s", 3, 3)])
ok("sheets: a layout that needs a non-guillotine cut is caught", any("straight" in x for x in check_sheets(
    pj, [(0, None)], fake([(0, 0, 0, 2, 1, False), (1, 2, 0, 1, 2, False), (0, 1, 2, 2, 1, False),
                            (1, 0, 1, 1, 2, False), (2, 1, 1, 1, 1, False)]))))

print("Jobs, Excel and reports")
for demo in (jobs.demo_bars(), jobs.demo_sheets()):
    back = jobs.from_excel(jobs.to_excel(demo))
    a, _ = jobs.clean(demo)
    b, _ = jobs.clean(back)
    same = (a.mode == b.mode and a.kerf == b.kerf and a.trim == b.trim and a.min_offcut == b.min_offcut and
            a.stock.astype(str).equals(b.stock.astype(str)) and a.pieces.astype(str).equals(b.pieces.astype(str)))
    ok(f"Excel round trip keeps the {demo.mode} job", same)
broken = jobs.demo_sheets()
broken.pieces.loc[0, "Length"] = -5
broken.pieces.loc[1, "Qty"] = 0
broken.pieces.loc[2, "Material"] = "Glass 6 mm"
_, probs = jobs.clean(broken)
ok("job problems are reported (negative size, zero quantity, material without stock)",
   any("Length" in x for x in probs) and any("quantity" in x for x in probs) and any("Glass" in x for x in probs),
   str(probs))
try:
    jobs.from_excel(jobs.to_excel(jobs.Job("bars", pd.DataFrame({"Material": ["a"]}), pd.DataFrame({"Name": ["x"]}))))
    ok("a workbook with missing columns is rejected", False)
except ValueError as exc:
    ok("a workbook with missing columns is rejected", "missing" in str(exc))
res_b = jobs.plan(jobs.clean(jobs.demo_bars())[0], seconds=10)
sv = report.savings(res_b)
ok("window demo: valid, proven best, cheaper than by hand",
   not res_b.problems and bool(sv["table"]["Proven best"].all()) and sv["planned"] < sv["by_hand"] <= sv["as_listed"],
   str({k: v for k, v in sv.items() if k != "table"}))
res_s = jobs.plan(jobs.clean(jobs.demo_sheets())[0], seconds=10)
sv = report.savings(res_s)
ok("kitchen demo: valid, white board at its lower bound, cheaper than by hand",
   not res_s.problems and sv["planned"] < sv["by_hand"] and bool(sv["table"]["Proven best"].iloc[0]),
   str({k: v for k, v in sv.items() if k != "table"}))
pdf = report.pdf(res_s)
ok("cut sheets PDF: one page per sheet pattern plus a summary",
   pdf[:4] == b"%PDF" and len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 1 + len(report.cut_list(res_s)))
xl = pd.ExcelFile(io.BytesIO(report.excel(res_b)))
chk = pd.read_excel(xl, "Pieces check")
ok("cut list workbook: every piece cut as ordered", set(xl.sheet_names) >= {"Summary", "Cut list", "Pieces check",
                                                                            "Offcuts kept"} and
   bool((chk["Ordered"] == chk["Cut"]).all()))
more = jobs.add_offcuts(jobs.clean(jobs.demo_bars())[0], res_b)
added = more.stock[more.stock["Name"].str.contains("this job")]
ok("kept offcuts become free stock for the next job", len(added) > 0 and (added["Cost"] == 0).all() and
   int(added["Available"].sum()) == int(report.offcuts(res_b)["Count"].sum()))
fig = report.bar_figure(*res_b.parts["Frame"][:2])
ok("bar diagram: one row per pattern", len(fig.layout.yaxis.tickvals) == len(res_b.parts["Frame"][1].patterns()))

print("Benchmark data")
ok("optimal values of all 1,615 bar instances are known", len(data.OPTIMAL) == 1615)
if not QUICK and os.path.isdir(os.path.join(data.CACHE, "bpp", "5_Hard28")):
    files = data.bpp_files()
    ok("BPPLIB reader: 1,615 instances in five families", sum(len(v) for v in files.values()) == 1615)
    picks = ["Falkenauer_u120_00.txt", "Falkenauer_t60_00.txt", "N1C1W1_A.txt", "Waescher_TEST0005.txt",
             "Schwerin1_BPP1.txt", "Hard28_BPP14.txt"]
    paths = {os.path.basename(p): p for v in files.values() for p in v}
    got = []
    for name in picks:
        job = data.read_bpp(paths[name])
        plan = B.solve(job, seconds=10)
        got.append((name, int(plan.cost), data.OPTIMAL[name], not check_bars(job, plan.bars)))
    ok("published optima reached (one instance of each kind)", all(c == o and v for _, c, o, v in got), str(got))
    if os.path.isdir(os.path.join(data.CACHE, "class")):
        cj = data.class_instance(1)
        ok("CLASS reader: 20 panels of class 1", sum(x.qty for x in cj.panels) == 20 and cj.sheets[0].width == 10)

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
