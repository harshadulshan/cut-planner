"""
Mutation test: do the engine checks notice when the code is wrong?

Each mutant copies the package, plants one bug (the checker forgetting the saw kerf, the solver turning panels against
the grain, offcuts used beyond what the rack holds ...) and runs `verify_engine.py --quick` against the copy. Every
mutant must be caught.

    python tests/mutation_test.py            # about 40 minutes
    python tests/mutation_test.py grain      # only the mutants whose name contains "grain"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTANTS = [
    ("checker ignores the saw kerf on bars", "checks.py",
     "need = sum(job.pieces[i].length for i in ps) + job.kerf * max(0, len(ps) - 1)",
     "need = sum(job.pieces[i].length for i in ps)"),
    ("checker ignores the stock available", "checks.py",
     "if b.available is not None and used[s] > b.available:", "if False:"),
    ("checker misses panels turned against the grain", "checks.py", "if r and not p.rotate:", "if False:"),
    ("checker's guillotine test always passes", "checks.py", "    if len(rects) <= 1:\n        return True",
     "    if True:\n        return True"),
    ("solver ignores the saw kerf on bars", "bars.py", "        k = int(round(self.kerf / self.unit))",
     "        k = 0"),
    ("solver ignores the trim on bars", "bars.py",
     "caps = [int(math.floor((b.length - self.trim) / self.unit + 1e-9)) + k for b in self.stock]",
     "caps = [int(math.floor(b.length / self.unit + 1e-9)) + k for b in self.stock]"),
    ("knapsack never uses a piece's full count", "bars.py", "        b = int(min(bounds[i], cap // weights[i]))",
     "        b = int(min(bounds[i], cap // weights[i])) - (1 if bounds[i] > 1 else 0)"),
    ("lower bound one bar too high", "bars.py", "    lb = max(round_bound(bound, list(cost)), 0)",
     "    lb = max(round_bound(bound, list(cost)), 0) + 1"),
    ("offcuts used beyond what the rack holds", "bars.py",
     "res = linprog(c, A_ub=A_ub if lim else None, b_ub=b_ub if lim else None, A_eq=A_eq, b_eq=b_eq,",
     "res = linprog(c, A_eq=A_eq, b_eq=b_eq,"),
    ("extra pieces left in the plan", "bars.py", "            if need[i] > 0:", "            if True:"),
    ("offcut length forgets the last kerf", "bars.py",
     "    off = end - pos - (job.kerf if pos < end - 1e-9 else 0)", "    off = end - pos"),
    ("sheet layouts ignore the saw kerf", "sheets.py", "        k = int(round(self.kerf))\n        dims",
     "        k = 0\n        dims"),
    ("sheet planner turns panels against the grain", "sheets.py", "    rot = [p.rotate for p in job.panels]",
     "    rot = [True for p in job.panels]"),
    ("sheet drawing ignores the trim", "sheets.py", "    y = job.trim\n", "    y = 0.0\n"),
    ("Excel loses the grain", "jobs.py",
     'pieces["Grain"] = ["Yes" if _yes(g) else "No" for g in pieces["Grain"]]', 'pieces["Grain"] = "No"'),
]


def main():
    only = sys.argv[1:]
    todo = [m for m in MUTANTS if not only or any(w in m[0] for w in only)]
    caught = 0
    for name, fname, old, new in todo:
        tmp = tempfile.mkdtemp(prefix="mutant_")
        try:
            shutil.copytree(os.path.join(ROOT, "cutplan"), os.path.join(tmp, "cutplan"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            if os.path.isdir(os.path.join(ROOT, "data_cache")):
                os.symlink(os.path.join(ROOT, "data_cache"), os.path.join(tmp, "data_cache"))
            path = os.path.join(tmp, "cutplan", fname)
            src = open(path).read()
            assert src.count(old) == 1, f"{name}: the text to mutate is not found exactly once"
            open(path, "w").write(src.replace(old, new))
            try:
                res = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tests", "verify_engine.py"), "--quick"],
                                     env=dict(os.environ, CP_ROOT=tmp), capture_output=True, text=True, timeout=1200)
            except subprocess.TimeoutExpired:
                print(f"  MISSED  {name:46s}  (checks did not finish)", flush=True)
                continue
            fails = [ln.strip()[6:] for ln in res.stdout.splitlines() if ln.strip().startswith("FAIL")]
            hit = res.returncode != 0
            caught += hit
            why = fails[0] if fails else (res.stderr.strip().splitlines() or ["crash"])[-1]
            print(f"  {'CAUGHT' if hit else 'MISSED'}  {name:46s}  {why[:90] if hit else ''}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{caught} of {len(todo)} mutants caught")
    sys.exit(0 if caught == len(todo) else 1)


if __name__ == "__main__":
    main()
