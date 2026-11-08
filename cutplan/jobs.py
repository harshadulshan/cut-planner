"""
A workshop job: settings, stock and pieces, as tables (pandas) and as an Excel workbook, split by material so that
every material (a profile, a board) is planned on its own. Also the two fictional demo jobs.

Bars:   Stock  = Material, Name, Length, Cost, Available        Pieces = Material, Name, Length, Qty
Sheets: Stock  = Material, Name, Length, Width, Cost, Available Panels = Material, Name, Length, Width, Qty, Grain
A sheet's Length runs along its grain; a panel with Grain = Yes keeps its Length along the sheet's Length.
"""

from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import bars as B
from . import sheets as S
from .checks import check_bars, check_sheets

BAR_STOCK = ["Material", "Name", "Length", "Cost", "Available"]
BAR_PIECES = ["Material", "Name", "Length", "Qty"]
SHEET_STOCK = ["Material", "Name", "Length", "Width", "Cost", "Available"]
SHEET_PANELS = ["Material", "Name", "Length", "Width", "Qty", "Grain"]
DEFAULTS = {"bars": {"kerf": 4.0, "trim": 20.0, "min_offcut": 400.0},
            "sheets": {"kerf": 4.0, "trim": 10.0, "min_offcut": 0.0}}


@dataclass
class Job:
    mode: str                                   # "bars" or "sheets"
    stock: pd.DataFrame
    pieces: pd.DataFrame
    kerf: float = 4.0
    trim: float = 0.0
    min_offcut: float = 0.0
    currency: str = "$"
    name: str = "Job"

    def materials(self):
        return [m for m in dict.fromkeys(self.pieces["Material"].astype(str)) if m]


@dataclass
class Result:
    job: Job
    parts: dict                                 # material -> (engine job, plan, by hand, as listed)
    seconds: float
    problems: list = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ cleaning
def _yes(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("yes", "y", "true", "1", "x")
    return bool(v) and not (isinstance(v, float) and math.isnan(v))


def _num(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def clean(job: Job) -> tuple[Job, list]:
    """Drop empty rows, check numbers; returns the cleaned job and a list of problems (empty = fine)."""
    problems = []
    st = job.stock.copy()
    pc = job.pieces.copy()
    st["Material"] = st["Material"].fillna("").astype(str).str.strip()
    pc["Material"] = pc["Material"].fillna("").astype(str).str.strip()
    st["Name"] = st["Name"].fillna("").astype(str).str.strip()
    pc["Name"] = pc["Name"].fillna("").astype(str).str.strip()
    dims = ["Length"] if job.mode == "bars" else ["Length", "Width"]
    st = st[st[dims].notna().any(axis=1) | (st["Name"] != "")]
    pc = pc[pc[dims + ["Qty"]].notna().any(axis=1) | (pc["Name"] != "")]
    for k, r in st.iterrows():
        for c in dims:
            if not (_num(r[c], 0) > 0):
                problems.append(f"Stock '{r['Name'] or k + 1}': {c} must be a positive number")
        if _num(r.get("Cost"), 0) < 0:
            problems.append(f"Stock '{r['Name'] or k + 1}': cost cannot be negative")
        a = _num(r.get("Available"))
        if a is not None and (a < 0 or a != int(a)):
            problems.append(f"Stock '{r['Name'] or k + 1}': available must be a whole number (or empty for unlimited)")
    for k, r in pc.iterrows():
        for c in dims:
            if not (_num(r[c], 0) > 0):
                problems.append(f"Piece '{r['Name'] or k + 1}': {c} must be a positive number")
        q = _num(r["Qty"], 0)
        if not (q >= 1 and q == int(q)):
            problems.append(f"Piece '{r['Name'] or k + 1}': quantity must be a whole number, at least 1")
    stock_mats = set(st["Material"])
    for m in dict.fromkeys(pc["Material"]):
        if m not in stock_mats:
            problems.append(f"Material '{m}': pieces need it but there is no stock of it")
    for c, lo in (("kerf", 0), ("trim", 0), ("min_offcut", 0)):
        if _num(getattr(job, c), -1) < lo:
            problems.append(f"Settings: {c.replace('_', ' ')} must be 0 or more")
    out = Job(job.mode, st.reset_index(drop=True), pc.reset_index(drop=True), float(_num(job.kerf, 0)),
              float(_num(job.trim, 0)), float(_num(job.min_offcut, 0)), job.currency, job.name)
    return out, problems


def engine_jobs(job: Job) -> dict:
    """{material: BarJob or SheetJob} (the job must be clean)."""
    out = {}
    for m in job.materials():
        st = job.stock[job.stock["Material"] == m]
        pc = job.pieces[job.pieces["Material"] == m]
        avail = [None if _num(a) is None else int(_num(a)) for a in st["Available"]]
        cost = [_num(c, 0.0) for c in st["Cost"]]
        if job.mode == "bars":
            stock = [B.Bar(str(n) or f"Bar {k + 1}", float(L), c, a)
                     for k, (n, L, c, a) in enumerate(zip(st["Name"], st["Length"], cost, avail))]
            pieces = [B.Piece(str(n) or f"Piece {k + 1}", float(L), int(q))
                      for k, (n, L, q) in enumerate(zip(pc["Name"], pc["Length"], pc["Qty"]))]
            out[m] = B.BarJob(pieces, stock, kerf=job.kerf, trim=job.trim, min_offcut=job.min_offcut, name=m)
        else:
            stock = [S.Sheet(str(n) or f"Sheet {k + 1}", float(L), float(W), c, a)
                     for k, (n, L, W, c, a) in enumerate(zip(st["Name"], st["Length"], st["Width"], cost, avail))]
            panels = [S.Panel(str(n) or f"Panel {k + 1}", float(L), float(W), int(q), rotate=not _yes(g))
                      for k, (n, L, W, q, g) in enumerate(zip(pc["Name"], pc["Length"], pc["Width"], pc["Qty"],
                                                              pc["Grain"]))]
            out[m] = S.SheetJob(panels, stock, kerf=job.kerf, trim=job.trim, name=m)
    return out


def by_hand(ej, careful=True):
    """The plan a cutter makes by hand. Careful: longest pieces first, into the first bar they fit in (bars); tallest
    panels first, strip by strip (sheets). Otherwise the same, but in the order of the list."""
    if isinstance(ej, B.BarJob):
        return B.ffd(ej, decreasing=careful)
    return S.shelf_layouts(ej, decreasing=careful)


def plan(job: Job, seconds: float = 30.0, progress=None) -> Result:
    """Plan every material; the time is shared out by the number of pieces (at least 2 s each)."""
    t0 = time.time()
    ejs = engine_jobs(job)
    sizes = {m: sum(p.qty for p in (e.pieces if isinstance(e, B.BarJob) else e.panels)) for m, e in ejs.items()}
    total = sum(sizes.values()) or 1
    parts, problems = {}, []
    for k, (m, e) in enumerate(ejs.items()):
        secs = max(2.0, seconds * sizes[m] / total)

        def prog(ev, m=m, k=k):
            if progress:
                progress(m, k, len(ejs), ev)
        if isinstance(e, B.BarJob):
            p = B.solve(e, seconds=secs, progress=prog)
            problems += [f"{m}: {x}" for x in check_bars(e, p.bars)]
        else:
            p = S.solve(e, seconds=secs, progress=prog)
            problems += [f"{m}: {x}" for x in check_sheets(e, p.sheets, S.rectangles)]
        parts[m] = (e, p, by_hand(e), by_hand(e, careful=False))
    return Result(job, parts, time.time() - t0, problems)


def items_of(plan):
    return plan.bars if hasattr(plan, "bars") else plan.sheets


def _stats(e, items):
    if isinstance(e, B.BarJob):
        s = B.summary(e, items or [])
        return {"n": s["bars"], "cost": s["cost"], "stock": s["stock_length"] / 1000,
                "waste": 100 * (1 - s["yield_pct"]) if s["bars"] else 0.0, "kept": s["kept_length"] / 1000}
    s = S.summary(e, items or [])
    return {"n": s["sheets"], "cost": s["cost"], "stock": s["sheet_area"] / 1e6, "waste": 100 * s["waste_pct"],
            "kept": 0.0}


def compare(res: Result) -> pd.DataFrame:
    """Per material: stock used, cost and waste of the plan, by hand (careful) and as listed."""
    rows = []
    for m, (e, p, hand, listed) in res.parts.items():
        a, h, li = _stats(e, items_of(p)), _stats(e, hand), _stats(e, listed)
        bars = isinstance(e, B.BarJob)
        rows.append({"Material": m, "Unit": "bars" if bars else "sheets", "Stock unit": "m" if bars else "m²",
                     "Planned": a["n"], "By hand": h["n"], "As listed": li["n"],
                     "Planned cost": a["cost"], "By hand cost": h["cost"], "As listed cost": li["cost"],
                     "Planned stock": a["stock"], "By hand stock": h["stock"], "As listed stock": li["stock"],
                     "Planned waste %": a["waste"], "By hand waste %": h["waste"], "As listed waste %": li["waste"],
                     "Offcuts kept": a["kept"], "Lower bound": p.lower_bound, "Proven best": p.status == "optimal",
                     "Hand ok": hand is not None, "Listed ok": listed is not None})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ Excel
def to_excel(job: Job) -> bytes:
    bio = io.BytesIO()
    settings = pd.DataFrame({"Setting": ["Job name", "Type", "Saw kerf (mm)", "Trim (mm)", "Keep offcuts from (mm)",
                                         "Currency"],
                             "Value": [job.name, job.mode, job.kerf, job.trim, job.min_offcut, job.currency]})
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        settings.to_excel(xw, sheet_name="Settings", index=False)
        job.stock.to_excel(xw, sheet_name="Stock", index=False)
        job.pieces.to_excel(xw, sheet_name="Pieces" if job.mode == "bars" else "Panels", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(12, min(40, max(len(str(c.value or ""))
                                                                                          for c in col) + 2))
    return bio.getvalue()


def from_excel(data) -> Job:
    xl = pd.ExcelFile(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    s = pd.read_excel(xl, "Settings")
    kv = {str(a).strip().lower(): b for a, b in zip(s.iloc[:, 0], s.iloc[:, 1])}
    mode = str(kv.get("type", "bars")).strip().lower()
    if mode not in ("bars", "sheets"):
        raise ValueError("Settings: Type must be 'bars' or 'sheets'")
    stock = pd.read_excel(xl, "Stock")
    pieces = pd.read_excel(xl, "Pieces" if mode == "bars" else "Panels")
    need_s, need_p = (BAR_STOCK, BAR_PIECES) if mode == "bars" else (SHEET_STOCK, SHEET_PANELS)
    for df, need, sheet in ((stock, need_s, "Stock"), (pieces, need_p, "Pieces" if mode == "bars" else "Panels")):
        miss = [c for c in need if c not in df.columns]
        if miss:
            raise ValueError(f"sheet '{sheet}' is missing the column(s): {', '.join(miss)}")
    stock = stock[need_s]
    pieces = pieces[need_p].copy()
    if mode == "sheets":
        pieces["Grain"] = ["Yes" if _yes(g) else "No" for g in pieces["Grain"]]
    d = DEFAULTS[mode]
    return Job(mode, stock, pieces, kerf=_num(kv.get("saw kerf (mm)"), d["kerf"]),
               trim=_num(kv.get("trim (mm)"), d["trim"]), min_offcut=_num(kv.get("keep offcuts from (mm)"),
                                                                           d["min_offcut"]),
               currency=str(kv.get("currency", "$")) if str(kv.get("currency", "")) not in ("", "nan") else "$",
               name=str(kv.get("job name", "Job")))


# ------------------------------------------------------------------------------------------------ demo jobs
def demo_bars() -> Job:
    """A fictional aluminium workshop order: 11 sliding windows for one house (frames and sashes), 6 m profiles, a few
    frame offcuts from earlier jobs in the rack. Prices are made up; enter your own."""
    windows = [("W1 living room", 1100, 1400, 2), ("W2 dining", 1750, 1350, 1), ("W3 kitchen", 2100, 650, 2),
               ("W4 bedroom 1", 1850, 1600, 1), ("W5 bedroom 2", 1100, 1400, 1), ("W6 stairs", 600, 1800, 1),
               ("W7 hall", 2300, 1450, 1), ("W8 bedroom 3", 1850, 1400, 2)]
    pieces = []
    for name, w, h, n in windows:
        tag = name.split()[0]
        pieces += [("Frame", f"{tag} frame top/bottom", w, 2 * n), ("Frame", f"{tag} frame side", h, 2 * n)]
    for name, w, h, n in windows:
        tag = name.split()[0]
        pieces += [("Sash", f"{tag} sash rail", w // 2 + 25, 4 * n), ("Sash", f"{tag} sash stile", h - 40, 4 * n)]
    stock = pd.DataFrame([("Frame", "Frame profile 6 m", 6000, 42, None),
                          ("Frame", "Frame offcut 2650 (rack)", 2650, 0, 1),
                          ("Frame", "Frame offcut 1500 (rack)", 1500, 0, 2),
                          ("Sash", "Sash profile 6 m", 6000, 35, None)], columns=BAR_STOCK)
    return Job("bars", stock, pd.DataFrame(pieces, columns=BAR_PIECES), kerf=4.0, trim=20.0, min_offcut=400.0,
               currency="$", name="Demo: 11 sliding windows (fictional)")


def demo_sheets() -> Job:
    """A fictional kitchen: carcasses in white melamine board (no grain, panels may turn), doors, drawer fronts and
    end panels in oak-grain board (grain must run along the length). Prices are made up; enter your own."""
    W, O = "White board 18 mm", "Oak board 18 mm"
    panels = [(W, "Base side", 720, 560, 12, "No"), (W, "Base bottom 600", 564, 560, 4, "No"),
              (W, "Base bottom 400", 364, 560, 2, "No"), (W, "Base shelf 600", 564, 500, 4, "No"),
              (W, "Base shelf 400", 364, 500, 2, "No"), (W, "Base rail 600", 564, 100, 8, "No"),
              (W, "Base rail 400", 364, 100, 4, "No"), (W, "Wall side", 720, 300, 8, "No"),
              (W, "Wall top/bottom", 564, 300, 8, "No"), (W, "Wall shelf", 564, 280, 4, "No"),
              (W, "Tall side", 2100, 560, 2, "No"), (W, "Tall shelf", 564, 540, 5, "No"),
              (O, "Base door", 716, 297, 8, "Yes"), (O, "Base door 400", 716, 397, 2, "Yes"),
              (O, "Wall door", 716, 297, 8, "Yes"), (O, "Tall door", 2096, 597, 1, "Yes"),
              (O, "End panel", 870, 600, 2, "Yes"), (O, "Drawer front", 597, 176, 6, "Yes")]
    stock = pd.DataFrame([(W, "White board 2440 x 1220", 2440, 1220, 48, None),
                          (O, "Oak board 2440 x 1220", 2440, 1220, 74, None)], columns=SHEET_STOCK)
    return Job("sheets", stock, pd.DataFrame(panels, columns=SHEET_PANELS), kerf=4.0, trim=10.0, min_offcut=0.0,
               currency="$", name="Demo: kitchen cabinets (fictional)")


def empty(mode: str) -> Job:
    d = DEFAULTS[mode]
    if mode == "bars":
        return Job(mode, pd.DataFrame(columns=BAR_STOCK), pd.DataFrame(columns=BAR_PIECES), **d, name="New job")
    return Job(mode, pd.DataFrame(columns=SHEET_STOCK), pd.DataFrame(columns=SHEET_PANELS), **d, name="New job")


def add_offcuts(job: Job, res: Result) -> Job:
    """Kept offcuts become stock (free, limited to how many there are) for the next job."""
    rows = []
    for m, (e, p, *_rest) in res.parts.items():
        if not isinstance(e, B.BarJob) or job.min_offcut <= 0:
            continue
        lens = sorted((round(B.bar_layout(e, s, ps)["offcut"]) for s, ps in p.bars), reverse=True)
        for L, n in pd.Series([x for x in lens if x >= job.min_offcut], dtype=float).value_counts().items():
            rows.append((m, f"Offcut {int(L)} (this job)", int(L), 0, int(n)))
    st = pd.concat([job.stock, pd.DataFrame(rows, columns=BAR_STOCK)], ignore_index=True) if rows else job.stock
    return Job(job.mode, st, job.pieces, job.kerf, job.trim, job.min_offcut, job.currency, job.name)


__all__ = ["Job", "Result", "clean", "engine_jobs", "plan", "compare", "to_excel", "from_excel", "demo_bars",
           "demo_sheets", "empty", "add_offcuts", "by_hand", "items_of", "np"]
