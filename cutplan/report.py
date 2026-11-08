"""
What the workshop gets: cutting diagrams (interactive, and as a printable PDF), the cut list as Excel, and the
comparison with cutting by hand.
"""

from __future__ import annotations

import io
import string

import pandas as pd
import plotly.graph_objects as go

from . import bars as B
from . import sheets as S
from .jobs import Result, compare, items_of

PIECE = ["#cfe1f7", "#9fc3ee"]          # alternate so neighbouring pieces stand apart; the label names the piece
EDGE = "#2a78d6"
KEEP, KEEP_EDGE = "#cdeee2", "#1baf7a"
SCRAP, SCRAP_EDGE = "#e4e3df", "#9b9a95"
INK, MUTED = "#1f1f1e", "#6b6a66"


def letters(k):
    a = string.ascii_uppercase
    return a[k] if k < 26 else a[k // 26 - 1] + a[k % 26]


def fmt(x):
    return f"{x:,.0f}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"


# ------------------------------------------------------------------------------------------------ cut lists
def bar_patterns(e: B.BarJob, plan) -> list:
    """[(letter, stock index, count, layout dict)] most used first."""
    out = []
    for k, (s, ps, n) in enumerate(plan.patterns()):
        out.append((letters(k), s, n, B.bar_layout(e, s, list(ps))))
    return out


def sheet_patterns(e: S.SheetJob, plan) -> list:
    out = []
    for k, (s, lay, n) in enumerate(plan.patterns()):
        out.append((letters(k), s, n, lay))
    return out


def cut_list(res: Result) -> pd.DataFrame:
    rows = []
    for m, (e, p, *_x) in res.parts.items():
        if isinstance(e, B.BarJob):
            for L, s, n, lay in bar_patterns(e, p):
                cuts = ", ".join(f"{e.pieces[i].name} {fmt(l)}" for i, _, l in lay["segments"])
                off = lay["offcut"]
                kind = "keep" if e.min_offcut > 0 and off >= e.min_offcut else "scrap"
                rows.append({"Material": m, "Pattern": L, "Count": n, "Stock": e.stock[s].name,
                             "Length": e.stock[s].length, "Cuts (in order)": cuts,
                             "Offcut": f"{fmt(off)} ({kind})" if off > 0 else "none"})
        else:
            for L, s, n, lay in sheet_patterns(e, p):
                cnt = pd.Series([e.panels[i].name for i, _ in S.pieces_of(lay)]).value_counts()
                used = sum(e.panels[i].width * e.panels[i].height for i, _ in S.pieces_of(lay))
                area = e.sheets[s].width * e.sheets[s].height
                rows.append({"Material": m, "Pattern": L, "Count": n, "Stock": e.sheets[s].name,
                             "Size": f"{fmt(e.sheets[s].width)} x {fmt(e.sheets[s].height)}",
                             "Panels": ", ".join(f"{k} x{v}" for k, v in cnt.items()),
                             "Used": f"{100 * used / area:.0f}%"})
    return pd.DataFrame(rows)


def offcuts(res: Result) -> pd.DataFrame:
    rows = []
    for m, (e, p, *_x) in res.parts.items():
        if isinstance(e, B.BarJob) and e.min_offcut > 0:
            for s, ps in p.bars:
                o = B.bar_layout(e, s, ps)["offcut"]
                if o >= e.min_offcut:
                    rows.append((m, round(o)))
    if not rows:
        return pd.DataFrame(columns=["Material", "Length", "Count"])
    df = pd.DataFrame(rows, columns=["Material", "Length"])
    return df.groupby(["Material", "Length"]).size().reset_index(name="Count").sort_values(
        ["Material", "Length"], ascending=[True, False])


def stock_to_take(res: Result) -> pd.DataFrame:
    rows = []
    for m, (e, p, *_x) in res.parts.items():
        st = e.stock if isinstance(e, B.BarJob) else e.sheets
        cnt = pd.Series([s for s, _ in items_of(p)]).value_counts()
        for s, n in cnt.items():
            rows.append({"Material": m, "Stock": st[s].name, "Count": int(n), "Cost each": st[s].cost,
                         "Cost": st[s].cost * int(n)})
    return pd.DataFrame(rows)


def savings(res: Result) -> dict:
    c = compare(res)
    ok = bool(c["Hand ok"].all()) and bool(c["Listed ok"].all())
    return {"planned": float(c["Planned cost"].sum()), "by_hand": float(c["By hand cost"].sum()),
            "as_listed": float(c["As listed cost"].sum()), "ok": ok,
            "planned_n": int(c["Planned"].sum()), "by_hand_n": int(c["By hand"].sum()),
            "as_listed_n": int(c["As listed"].sum()), "table": c}


# ------------------------------------------------------------------------------------------------ diagrams
def bar_figure(e: B.BarJob, plan, height_per=46):
    pats = bar_patterns(e, plan)
    fig = go.Figure()
    names = []
    for row, (L, s, n, lay) in enumerate(pats):
        y = len(pats) - 1 - row
        names.append(f"<b>{L}</b>  ×{n}<br><span style='font-size:11px'>{e.stock[s].name}</span>")
        Ls = lay["length"]
        fig.add_shape(type="rect", x0=0, x1=Ls, y0=y - 0.32, y1=y + 0.32, line=dict(color=SCRAP_EDGE, width=1),
                      fillcolor=SCRAP, layer="below")
        for k, (i, x, l) in enumerate(lay["segments"]):
            fig.add_shape(type="rect", x0=x, x1=x + l, y0=y - 0.32, y1=y + 0.32, line=dict(color=EDGE, width=1),
                          fillcolor=PIECE[k % 2])
            fig.add_trace(go.Scatter(x=[x + l / 2], y=[y], mode="text", text=[f"{fmt(l)}"], showlegend=False,
                                     textfont=dict(size=11, color=INK),
                                     hovertext=f"{e.pieces[i].name}: {fmt(l)} mm", hoverinfo="text"))
        off = lay["offcut"]
        if off > 0:
            keep = e.min_offcut > 0 and off >= e.min_offcut
            x0 = lay["offcut_start"]
            fig.add_shape(type="rect", x0=x0, x1=x0 + off, y0=y - 0.32, y1=y + 0.32,
                          line=dict(color=KEEP_EDGE if keep else SCRAP_EDGE, width=1, dash="dot"),
                          fillcolor=KEEP if keep else SCRAP)
            if off > Ls * 0.05:
                fig.add_trace(go.Scatter(x=[x0 + off / 2], y=[y], mode="text", showlegend=False,
                                         text=[f"{fmt(off)} {'keep' if keep else ''}".strip()],
                                         textfont=dict(size=10, color=KEEP_EDGE if keep else MUTED),
                                         hovertext=f"offcut {fmt(off)} mm{' (keep)' if keep else ' (scrap)'}",
                                         hoverinfo="text"))
    Lmax = max((lay["length"] for *_x, lay in pats), default=1)
    fig.update_xaxes(range=[-Lmax * 0.01, Lmax * 1.01], title="mm", showgrid=True, gridcolor="rgba(128,128,128,.18)",
                     zeroline=False)
    fig.update_yaxes(tickvals=list(range(len(pats))), ticktext=names[::-1], range=[-0.6, len(pats) - 0.4],
                     showgrid=False, zeroline=False)
    fig.update_layout(height=80 + height_per * len(pats), margin=dict(l=10, r=10, t=10, b=40),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", hoverlabel=dict(bgcolor="white"))
    return fig


def _label(name, dims, grain, pw, ph, cw=6.0):
    """The label that fits a panel drawn pw x ph pixels: name and size, turned for tall narrow panels, the size
    alone, or nothing (the hover still names it). Returns (text, angle)."""
    full = max(len(name), len(dims)) * cw
    lines = 3 if grain else 2
    for text, need, n in ((f"{name}<br>{dims}" + ("<br>↔ grain" if grain else ""), full, lines),
                          (f"{name}<br>{dims}", full, 2), (dims, len(dims) * cw, 1)):
        if need < pw - 6 and 13 * n < ph - 4:
            return text, 0
        if need < ph - 6 and 13 * n < pw - 4:
            return text, -90
    return "", 0


def sheet_figure(e: S.SheetJob, s: int, lay, title="", width=760):
    """One sheet pattern to scale: panels with names and sizes, trim (dotted), offcut (grey), first cuts (red,
    numbered). `width` is the width the chart will get on screen, in pixels."""
    rects, cuts, (W, H) = S.rectangles(e, s, lay)
    t = lay[0]
    x0, x1, y0, y1 = (-W * 0.005, W * 1.005, -H * 0.06, H * 1.01) if t else (-W * 0.03, W * 1.005, -H * 0.01,
                                                                             H * 1.01)
    top = 30 if title else 6
    ml, mr, mb = 26, 4, 34
    plot_w = width - ml - mr
    k_px = plot_w / (x1 - x0)
    plot_h = (y1 - y0) * k_px
    fig = go.Figure()
    fig.add_shape(type="rect", x0=0, y0=0, x1=W, y1=H, fillcolor=SCRAP, line=dict(color=SCRAP_EDGE, width=1),
                  layer="below")
    if e.trim > 0:
        fig.add_shape(type="rect", x0=e.trim, y0=e.trim, x1=W - e.trim, y1=H - e.trim, fillcolor="#f2f1ee",
                      line=dict(color=SCRAP_EDGE, width=1, dash="dot"), layer="below")
    xs, ys, hv = [], [], []
    for k, (i, x, y, w, h, r) in enumerate(rects):
        p = e.panels[i]
        fig.add_shape(type="rect", x0=x, y0=y, x1=x + w, y1=y + h, fillcolor=PIECE[k % 2],
                      line=dict(color=EDGE, width=1))
        xs.append(x + w / 2)
        ys.append(y + h / 2)
        text, angle = _label(p.name, f"{fmt(p.width)} × {fmt(p.height)}", not p.rotate, w * k_px, h * k_px)
        if text:
            fig.add_annotation(x=x + w / 2, y=y + h / 2, text=text, showarrow=False, textangle=angle,
                               font=dict(size=10, color=INK))
        hv.append(f"{p.name}: {fmt(p.width)} × {fmt(p.height)} mm{' (turned)' if r else ''}"
                  f"{' · grain along the length' if not p.rotate else ''}")
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", marker=dict(size=18, opacity=0), hovertext=hv,
                             hoverinfo="text", showlegend=False))
    for k, c in enumerate(cuts[:-1] if cuts else []):
        if t:
            fig.add_shape(type="line", x0=c + e.kerf / 2, x1=c + e.kerf / 2, y0=0, y1=H,
                          line=dict(color="#e34948", width=1.5, dash="dash"))
            fig.add_annotation(x=c + e.kerf / 2, y=-H * 0.03, text=f"<b>{k + 1}</b>", showarrow=False,
                               font=dict(size=11, color="#e34948"))
        else:
            fig.add_shape(type="line", x0=0, x1=W, y0=c + e.kerf / 2, y1=c + e.kerf / 2,
                          line=dict(color="#e34948", width=1.5, dash="dash"))
            fig.add_annotation(x=-W * 0.015, y=c + e.kerf / 2, text=f"<b>{k + 1}</b>", showarrow=False,
                               font=dict(size=11, color="#e34948"))
    # a fixed size with fixed margins keeps the drawing to scale (one millimetre is as long across as down)
    fig.update_xaxes(range=[x0, x1], showgrid=False, zeroline=False, showticklabels=False, automargin=False,
                     title=dict(text=f"{fmt(W)} mm · length, along the grain", font=dict(size=11), standoff=6))
    fig.update_yaxes(range=[y1, y0], showgrid=False, zeroline=False, showticklabels=False, automargin=False,
                     title=dict(text=f"{fmt(H)} mm", font=dict(size=11), standoff=4))
    fig.update_layout(width=int(width), height=int(round(plot_h)) + top + mb, autosize=False,
                      margin=dict(l=ml, r=mr, t=top, b=mb), title=dict(text=title, font=dict(size=14)),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", hoverlabel=dict(bgcolor="white"))
    return fig


def strip_cuts_text(e: S.SheetJob, lay) -> str:
    """First cuts, in plain words: rip the sheet into strips of these sizes."""
    t, strips = lay
    k = int(round(e.kerf))
    sizes = [fmt(h - k) for h, _ in strips]
    return (f"First cuts (red, numbered) {'across the length' if t else 'along the length'}: strips of "
            f"{', '.join(sizes)} mm; then cut each strip into its pieces.")


# ------------------------------------------------------------------------------------------------ PDF
def pdf(res: Result) -> bytes:
    """Cut sheets for the workshop: a summary page, then the bar patterns (eight to a page) and one page per sheet
    pattern, each with how many to cut and boxes to tick."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.patches import Rectangle

    bio = io.BytesIO()
    cur = res.job.currency
    with PdfPages(bio) as pp:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.95, res.job.name, fontsize=16, weight="bold")
        fig.text(0.07, 0.925, f"Saw kerf {fmt(res.job.kerf)} mm · trim {fmt(res.job.trim)} mm"
                 + (f" · offcuts from {fmt(res.job.min_offcut)} mm are kept" if res.job.mode == "bars"
                    and res.job.min_offcut > 0 else ""), fontsize=9, color=MUTED)
        y = 0.88
        fig.text(0.07, y, "Stock to take", fontsize=12, weight="bold")
        y -= 0.025
        for _, r in stock_to_take(res).iterrows():
            fig.text(0.09, y, f"☐  {r['Count']} × {r['Stock']}  ({r['Material']})", fontsize=10)
            y -= 0.022
        sv = savings(res)
        y -= 0.02
        fig.text(0.07, y, "Material cost", fontsize=12, weight="bold")
        y -= 0.025
        fig.text(0.09, y, f"This plan {cur}{fmt(sv['planned'])}  ·  by hand, longest first {cur}{fmt(sv['by_hand'])}"
                 f"  ·  saved {cur}{fmt(sv['by_hand'] - sv['planned'])}", fontsize=10)
        y -= 0.04
        fig.text(0.07, y, "Patterns", fontsize=12, weight="bold")
        y -= 0.025
        for _, r in cut_list(res).iterrows():
            if y < 0.06:
                break
            extra = r.get("Offcut", r.get("Used", ""))
            fig.text(0.09, y, f"{r['Material']} {r['Pattern']}: {r['Count']} × {r['Stock']}  ({extra})", fontsize=9)
            y -= 0.02
        pp.savefig(fig)
        plt.close(fig)

        for m, (e, p, *_x) in res.parts.items():
            if isinstance(e, B.BarJob):
                pats = bar_patterns(e, p)
                for start in range(0, len(pats), 8):
                    fig = plt.figure(figsize=(11.69, 8.27))
                    fig.text(0.04, 0.95, f"{m}: bar patterns", fontsize=14, weight="bold")
                    fig.text(0.04, 0.925, "Longest piece first; every cut removes the saw kerf. Tick a box for each "
                             "bar cut.", fontsize=9, color=MUTED)
                    for row, (L, s, n, lay) in enumerate(pats[start:start + 8]):
                        ax = fig.add_axes([0.04, 0.80 - row * 0.105, 0.92, 0.06])
                        Ls = lay["length"]
                        ax.add_patch(Rectangle((0, 0), Ls, 1, fc=SCRAP, ec=SCRAP_EDGE, lw=0.6))
                        for k, (i, x, l) in enumerate(lay["segments"]):
                            ax.add_patch(Rectangle((x, 0), l, 1, fc=PIECE[k % 2], ec=EDGE, lw=0.6))
                            ax.text(x + l / 2, 0.5, f"{e.pieces[i].name}\n{fmt(l)}" if l > Ls * 0.12 else fmt(l),
                                    ha="center", va="center", fontsize=6.5)
                        off = lay["offcut"]
                        if off > 0:
                            keep = e.min_offcut > 0 and off >= e.min_offcut
                            ax.add_patch(Rectangle((lay["offcut_start"], 0), off, 1, fc=KEEP if keep else SCRAP,
                                                   ec=KEEP_EDGE if keep else SCRAP_EDGE, lw=0.6, ls=":"))
                            if off > Ls * 0.05:
                                ax.text(lay["offcut_start"] + off / 2, 0.5, f"{fmt(off)}\n{'keep' if keep else ''}",
                                        ha="center", va="center", fontsize=6.5, color=KEEP_EDGE if keep else MUTED)
                        ax.set_xlim(0, Ls)
                        ax.set_ylim(0, 1)
                        ax.axis("off")
                        boxes = "☐ " * min(n, 30) + ("…" if n > 30 else "")
                        fig.text(0.04, 0.868 - row * 0.105, f"{L}   cut {n} × {e.stock[s].name} ({fmt(Ls)} mm)    "
                                 f"{boxes}", fontsize=9, weight="bold")
                    pp.savefig(fig)
                    plt.close(fig)
            else:
                for L, s, n, lay in sheet_patterns(e, p):
                    rects, cuts, (W, H) = S.rectangles(e, s, lay)
                    fig = plt.figure(figsize=(11.69, 8.27))
                    fig.text(0.04, 0.95, f"{m}: pattern {L}   cut {n} × {e.sheets[s].name}", fontsize=14,
                             weight="bold")
                    fig.text(0.04, 0.925, strip_cuts_text(e, lay) + "   " + "☐ " * min(n, 30), fontsize=9,
                             color=MUTED)
                    ax = fig.add_axes([0.04, 0.08, 0.92, 0.80])
                    ax.add_patch(Rectangle((0, 0), W, H, fc=SCRAP, ec=SCRAP_EDGE, lw=0.8))
                    for k, (i, x, y, w, h, r) in enumerate(rects):
                        pn = e.panels[i]
                        ax.add_patch(Rectangle((x, y), w, h, fc=PIECE[k % 2], ec=EDGE, lw=0.8))
                        small = min(w, h) < min(W, H) * 0.12
                        ax.text(x + w / 2, y + h / 2, f"{pn.name}\n{fmt(pn.width)} × {fmt(pn.height)}"
                                + ("\n↔ grain" if not pn.rotate and not small else ""), ha="center", va="center",
                                fontsize=5.5 if small else 7, rotation=90 if small and h > 1.5 * w else 0)
                    for k, c in enumerate(cuts[:-1]):
                        if lay[0]:
                            ax.plot([c + e.kerf / 2] * 2, [0, H], color="#e34948", lw=1, ls="--")
                            ax.text(c + e.kerf / 2, -H * 0.02, str(k + 1), color="#e34948", ha="center", va="top",
                                    fontsize=8)
                        else:
                            ax.plot([0, W], [c + e.kerf / 2] * 2, color="#e34948", lw=1, ls="--")
                            ax.text(-W * 0.006, c + e.kerf / 2, str(k + 1), color="#e34948", ha="right",
                                    va="center", fontsize=8)
                    ax.set_xlim(-W * 0.02, W * 1.01)
                    ax.set_ylim(H * 1.04, -H * 0.04)
                    ax.set_aspect("equal")
                    ax.axis("off")
                    pp.savefig(fig)
                    plt.close(fig)
    return bio.getvalue()


# ------------------------------------------------------------------------------------------------ Excel
def excel(res: Result) -> bytes:
    from openpyxl.styles import Font, PatternFill

    bio = io.BytesIO()
    sv = savings(res)
    cur = res.job.currency
    summary = pd.DataFrame({"": ["Job", "Material cost of this plan", "By hand (longest first)",
                                 "Saved", "Pieces all cut as ordered"],
                            "Value": [res.job.name, f"{cur}{fmt(sv['planned'])}", f"{cur}{fmt(sv['by_hand'])}",
                                      f"{cur}{fmt(sv['by_hand'] - sv['planned'])}",
                                      "yes (checked)" if not res.problems else "NO: " + "; ".join(res.problems)]})
    rows = []
    for m, (e, p, *_x) in res.parts.items():
        names = [x.name for x in (e.pieces if isinstance(e, B.BarJob) else e.panels)]
        got = pd.Series([i for _, it in items_of(p) for i in
                         (it if isinstance(e, B.BarJob) else [j for j, _ in S.pieces_of(it)])]).value_counts()
        for i, nm in enumerate(names):
            q = (e.pieces if isinstance(e, B.BarJob) else e.panels)[i].qty
            rows.append({"Material": m, "Piece": nm, "Ordered": q, "Cut": int(got.get(i, 0))})
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        stock_to_take(res).to_excel(xw, sheet_name="Stock to take", index=False)
        cut_list(res).to_excel(xw, sheet_name="Cut list", index=False)
        pd.DataFrame(rows).to_excel(xw, sheet_name="Pieces check", index=False)
        if res.job.mode == "bars":
            offcuts(res).to_excel(xw, sheet_name="Offcuts kept", index=False)
        head = PatternFill("solid", fgColor="DCE9F9")
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True)
                c.fill = head
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(10, min(90, max(len(str(c.value or ""))
                                                                                       for c in col) + 2))
    return bio.getvalue()
