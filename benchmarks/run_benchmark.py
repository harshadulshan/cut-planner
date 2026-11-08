"""
Benchmarks.

    python benchmarks/run_benchmark.py bars     # 1,615 BPPLIB instances (Falkenauer, Scholl, Waescher, Schwerin,
                                                # Hard28) against their proven optimal number of bins, 20 s each
    python benchmarks/run_benchmark.py sheets   # 500 CLASS two-dimensional instances against a lower bound, 10 s each

Two instances at a time (--workers), one search thread each. Every plan is re-checked by the independent checker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cutplan import bars, data, sheets  # noqa: E402
from cutplan.checks import check_bars, check_sheets  # noqa: E402


def run_bar(args):
    fam, path, seconds = args
    job = data.read_bpp(path)
    t0 = time.time()
    plan = bars.solve(job, seconds=seconds)
    hand = bars.ffd(job)
    name = os.path.basename(path)
    return {"name": name, "family": fam, "items": sum(p.qty for p in job.pieces), "capacity": job.stock[0].length,
            "bins": int(plan.cost), "optimal": data.OPTIMAL[name], "bound": int(plan.lower_bound),
            "proved": plan.status == "optimal", "ffd": len(hand), "seconds": round(time.time() - t0, 2),
            "valid": not check_bars(job, plan.bars)}


def run_sheet(args):
    k, seconds = args
    job = data.class_instance(k)
    t0 = time.time()
    plan = sheets.solve(job, seconds=seconds)
    hand = sheets.shelf_layouts(job)
    return {"name": job.name, "class": int(job.name[5:7]), "items": sum(p.qty for p in job.panels),
            "sheets": int(plan.cost), "bound": int(plan.lower_bound), "shelf": len(hand),
            "seconds": round(time.time() - t0, 2), "valid": not check_sheets(job, plan.sheets, sheets.rectangles)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["bars", "sheets"])
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--limit", type=int, default=0, help="only the first N instances of each family (quick test)")
    ap.add_argument("--workers", type=int, default=2, help="instances solved at the same time (one thread each)")
    a = ap.parse_args()
    t0 = time.time()
    if a.kind == "bars":
        secs = a.seconds or 20.0
        jobs = [(fam, p, secs) for fam, paths in data.bpp_files().items() for p in (paths[:a.limit] if a.limit else paths)]
        fn, out = run_bar, os.path.join(ROOT, "benchmarks", "results_bars.json")
    else:
        secs = a.seconds or 10.0
        jobs = [(k, secs) for k in range(1, 501)][: (a.limit or 500)]
        fn, out = run_sheet, os.path.join(ROOT, "benchmarks", "results_sheets.json")
    rows = []
    with Pool(a.workers) as pool:
        for k, r in enumerate(pool.imap_unordered(fn, jobs), 1):
            rows.append(r)
            if a.kind == "bars":
                flag = "=" if r["bins"] == r["optimal"] else f"+{r['bins'] - r['optimal']}"
                print(f"  {k:4d}/{len(jobs)} {r['name']:28s} bins {r['bins']:4d} optimum {r['optimal']:4d} {flag:3s} "
                      f"first fit {r['ffd']:4d}  {r['seconds']:5.1f}s valid {r['valid']}", flush=True)
            else:
                print(f"  {k:3d}/{len(jobs)} {r['name']:16s} sheets {r['sheets']:3d} bound {r['bound']:3d} shelf {r['shelf']:3d} "
                      f"{r['seconds']:5.1f}s valid {r['valid']}", flush=True)
    rows.sort(key=lambda r: r["name"])
    json.dump({"instances": rows, "seconds_per_instance": secs, "total_seconds": round(time.time() - t0),
               "machine": f"{os.cpu_count()} cores, {a.workers} instances at a time, one thread each"}, open(out, "w"), indent=0)


if __name__ == "__main__":
    main()
