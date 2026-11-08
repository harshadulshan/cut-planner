"""
Cut Planner
-----------
Plan the cuts for bars (aluminium and steel profiles, pipes, timber) and sheets (board, plywood, MDF, glass, sheet
metal) with the least waste: cutting diagrams to print, the cut list, offcuts kept for later, and the money saved
compared with cutting by hand. Tested on 1,615 published cutting benchmark instances with proven optimal answers.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cutplan import bars as B
from cutplan import jobs, report
from cutplan import sheets as S

st.set_page_config(page_title="Cut Planner", page_icon="✂️", layout="wide")

HERE = os.path.dirname(os.path.abspath(__file__))
C_PLAN, C_HAND, C_LIST, C_OK, C_BAD = "#2a78d6", "#9b9a95", "#d2d1cc", "#1baf7a", "#e34948"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ss = st.session_state
for key, default in {"job": None, "version": 0, "res": None, "res_sig": None, "seconds": 20,
                     "per_month": 8}.items():
    ss.setdefault(key, default)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def layout(fig, height=300, **kw):
    top = 60 if kw.get("title") else 20
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=top, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), **kw)
    fig.update_xaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False)
    return fig


def job_sig(job):
    return (job.mode, job.kerf, job.trim, job.min_offcut, job.stock.to_json(), job.pieces.to_json())


def load(job):
    ss.job, ss.res, ss.res_sig = job, None, None
    ss.version += 1


def money(x, cur=None):
    cur = ss.job.currency if cur is None and ss.job is not None else (cur or "")
    sign = "-" if x < -1e-9 else ""
    x = abs(x)
    return f"{sign}{cur}{x:,.0f}" if abs(x - round(x)) < 1e-6 or x >= 1000 else f"{sign}{cur}{x:,.2f}"


def plan_ok():
    return ss.res is not None and ss.job is not None and ss.res_sig == job_sig(ss.job)


def unit_word(job, n=2):
    return ("bar" if n == 1 else "bars") if job.mode == "bars" else ("sheet" if n == 1 else "sheets")


def waste_total(c, col):
    stock = c[col.replace("waste %", "stock")]
    return float((stock * c[col]).sum() / stock.sum()) if stock.sum() else 0.0


def as_plan(e, items):
    """A by-hand plan in the same shape as a solver plan, to draw it the same way."""
    if isinstance(e, B.BarJob):
        return B.BarPlan(items or [], 0.0, 0.0, "", 0.0)
    return S.SheetPlan(items or [], 0.0, 0.0, "", 0.0)


def files_for(res):
    """PDF and Excel of a plan, made once per plan."""
    if ss.get("_files", (None,))[0] is not res:
        ss["_files"] = (res, report.pdf(res), report.excel(res))
    return ss["_files"][1], ss["_files"][2]


def results_file(kind):
    path = os.path.join(HERE, "benchmarks", f"results_{kind}.json")
    return json.load(open(path)) if os.path.exists(path) else None


# ---------------------------------------------------------------------------
# sidebar + header
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ✂️ Cut Planner")
    st.caption("Less waste when cutting bars and sheets: cutting diagrams to print, a cut list, offcuts kept, money "
               "saved.")
    st.divider()
    side = st.container()
    st.divider()
    st.caption("Column generation with HiGHS and CP-SAT (Google OR-Tools). See **ℹ️ How it works**.")

st.title("✂️ Cut Planner")
st.caption("Aluminium, steel, timber, glass and furniture workshops lose material when cuts are planned by hand. Enter "
           "the pieces you need and the stock you have: get **cutting diagrams to print**, the **cut list**, the "
           "**offcuts worth keeping**, and the **money saved** compared with cutting by hand.")
tabs = st.tabs(["📥 Job", "⚙️ Plan", "✂️ Cutting diagrams", "💰 Savings", "🧪 Benchmark", "ℹ️ How it works"])

# ---------------------------------------------------------------------------
# 1. Job
# ---------------------------------------------------------------------------
with tabs[0]:
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Demo: aluminium windows (bars)**")
        st.caption("11 sliding windows for one house: frames and sashes from 6 m profiles, and a few frame offcuts "
                   "in the rack. Fictional order and prices.")
        if st.button("Load the window demo", type="primary", width="stretch"):
            load(jobs.demo_bars())
            st.rerun()
    with c2:
        st.markdown("**Demo: kitchen cabinets (sheets)**")
        st.caption("Carcasses in white board; doors, drawer fronts and end panels in oak board with the grain along "
                   "the length. Fictional order and prices.")
        if st.button("Load the kitchen demo", type="primary", width="stretch"):
            load(jobs.demo_sheets())
            st.rerun()
    with c3:
        st.markdown("**Your job**")
        st.caption("One Excel workbook: settings, stock and pieces. Start from a template (the demos).")
        up = st.file_uploader("Workbook", type=["xlsx"], label_visibility="collapsed")
        if up is not None and st.button(f"Use {up.name}", type="primary", width="stretch"):
            try:
                load(jobs.from_excel(up.getvalue()))
                st.rerun()
            except Exception as exc:
                st.error(f"Could not read {up.name}: {exc}")
        t1, t2 = st.columns(2)
        t1.download_button("Template: bars", jobs.to_excel(jobs.demo_bars()), "cut_template_bars.xlsx", XLSX,
                           width="stretch")
        t2.download_button("Template: sheets", jobs.to_excel(jobs.demo_sheets()), "cut_template_sheets.xlsx", XLSX,
                           width="stretch")
    job = ss.job
    if job is None:
        st.info("Load a demo or your own job to begin. Or start empty: "
                "use a template and replace the rows with your stock and pieces.")
        e1, e2, _ = st.columns([1, 1, 2])
        if e1.button("Empty bar job", width="stretch"):
            load(jobs.empty("bars"))
            st.rerun()
        if e2.button("Empty sheet job", width="stretch"):
            load(jobs.empty("sheets"))
            st.rerun()
    else:
        st.divider()
        bars_mode = job.mode == "bars"
        h1, h2, h3, h4, h5 = st.columns([3, 1, 1, 1, 1])
        name = h1.text_input("Job name", job.name, key=f"name_{ss.version}")
        kerf = h2.number_input("Saw kerf (mm)", 0.0, 20.0, float(job.kerf), 0.5, key=f"kerf_{ss.version}",
                               help="Material lost in every cut: the width of the blade.")
        trim = h3.number_input("Trim (mm)", 0.0, 500.0, float(job.trim), 1.0, key=f"trim_{ss.version}",
                               help=("Cut off each bar before use (both ends together), to square the ends."
                                     if bars_mode else "Taken off every edge of a sheet (damaged or uneven edges)."))
        min_off = h4.number_input("Keep offcuts from (mm)", 0.0, 10000.0, float(job.min_offcut), 50.0,
                                  key=f"off_{ss.version}", disabled=not bars_mode,
                                  help="Offcuts at least this long go back to the rack; 0 = no offcuts kept.")
        cur = h5.text_input("Currency", job.currency, key=f"cur_{ss.version}", max_chars=4)
        dims = {"Length": st.column_config.NumberColumn("Length (mm)", min_value=0.0, format="%.0f")}
        if not bars_mode:
            dims["Width"] = st.column_config.NumberColumn("Width (mm)", min_value=0.0, format="%.0f")
        s1, s2 = st.columns([2, 3])
        with s1:
            st.markdown("**Stock** · what you cut from")
            st.caption("Cost per " + ("bar" if bars_mode else "sheet") + "; *Available* empty = as many as needed. "
                       + ("Offcuts in the rack: cost 0, how many you have." if bars_mode else
                          "A sheet's length runs along its grain."))
            stock = st.data_editor(job.stock, num_rows="dynamic", hide_index=True, width="stretch",
                                   key=f"stock_{ss.version}", column_config={
                                       "Material": st.column_config.TextColumn("Material", required=True),
                                       **dims,
                                       "Cost": st.column_config.NumberColumn(f"Cost ({cur})", min_value=0.0,
                                                                             format="%.2f"),
                                       "Available": st.column_config.NumberColumn("Available", min_value=0,
                                                                                  step=1, format="%d")})
        with s2:
            st.markdown("**Pieces** · what you need")
            st.caption("The material must match a stock row." + ("" if bars_mode else
                       " *Grain* Yes = the panel's length must run along the sheet's length (it may not turn)."))
            cfg = {"Material": st.column_config.TextColumn("Material", required=True), **dims,
                   "Qty": st.column_config.NumberColumn("Qty", min_value=1, step=1, format="%d")}
            if not bars_mode:
                cfg["Grain"] = st.column_config.SelectboxColumn("Grain", options=["Yes", "No"], default="No")
            pieces = st.data_editor(job.pieces, num_rows="dynamic", hide_index=True, width="stretch",
                                    key=f"pieces_{ss.version}", column_config=cfg, height=420)
        new = dataclasses.replace(job, name=name, kerf=kerf, trim=trim, min_offcut=min_off, currency=cur,
                                  stock=stock, pieces=pieces)
        ss.job = new
        cj, problems = jobs.clean(new)
        if problems:
            st.warning("Please check: " + " · ".join(problems[:6]))
        else:
            m = st.columns(4)
            qty = int(cj.pieces["Qty"].sum())
            m[0].metric("Pieces", f"{qty:,}")
            m[1].metric("Piece types", len(cj.pieces))
            m[2].metric("Materials", len(cj.materials()))
            if bars_mode:
                m[3].metric("Total length of the pieces", f"{(cj.pieces['Length'] * cj.pieces['Qty']).sum() / 1000:,.1f} m")
            else:
                area = (cj.pieces["Length"] * cj.pieces["Width"] * cj.pieces["Qty"]).sum() / 1e6
                m[3].metric("Total area of the panels", f"{area:,.1f} m²")
        st.download_button("Save this job (Excel)", jobs.to_excel(new), f"{name or 'job'}.xlsx", XLSX)

# ---------------------------------------------------------------------------
# 2. Plan
# ---------------------------------------------------------------------------
with tabs[1]:
    job = ss.job
    if job is None:
        st.info("Load a job first (📥 Job).")
    else:
        b1, b2 = st.columns([1, 3])
        go_btn = b1.button("Plan the cuts", type="primary", width="stretch")
        b2.slider("Search time (seconds)", 5, 120, key="seconds",
                  help="Most jobs are proven best in a few seconds; large jobs improve with more time.")
        if go_btn:
            cj, problems = jobs.clean(job)
            if problems:
                st.error("Please fix the job first: " + " · ".join(problems[:6]))
            else:
                bar = st.progress(0.0, "Planning…")
                t0 = time.time()

                def prog(m, k, total, ev):
                    msg = (f"{m}: no plan can cost less than {money(ev[2])}" if ev[0] == "bound" else
                           f"{m}: best plan so far {money(ev[2])}")
                    bar.progress(min(1.0, (time.time() - t0) / ss.seconds), f"{m} ({k + 1} of {total}) · {msg}")

                try:
                    ss.res = jobs.plan(cj, seconds=float(ss.seconds), progress=prog)
                    ss.res_sig = job_sig(job)
                except ValueError as exc:
                    st.error(str(exc))
                bar.empty()
        if ss.res is None:
            st.info("Press **Plan the cuts**.")
        elif not plan_ok():
            st.warning("The job changed since this plan: plan the cuts again.")
        else:
            res = ss.res
            if res.problems:
                st.error("The independent check found problems: " + "; ".join(res.problems[:4]))
            else:
                st.success("✅ **Independent check passed:** every piece cut exactly as ordered; every "
                           + ("bar holds its pieces, a saw kerf at every cut and the trim."
                              if job.mode == "bars" else
                              "panel inside the trimmed sheet, saw kerf between panels, grain kept, and every sheet "
                              "cuttable with straight cuts from edge to edge."))
            sv = report.savings(res)
            c = sv["table"]
            u = unit_word(job)
            m = st.columns(4)
            m[0].metric(f"{u.capitalize()} used", sv["planned_n"],
                        delta=f"{sv['planned_n'] - sv['by_hand_n']:+d} vs by hand", delta_color="inverse")
            m[1].metric("Material cost", money(sv["planned"]),
                        delta=f"{money(sv['planned'] - sv['by_hand'])} vs by hand", delta_color="inverse")
            wp, wh = waste_total(c, "Planned waste %"), waste_total(c, "By hand waste %")
            m[2].metric("Left over", f"{wp:.1f}%", delta=f"{wp - wh:+.1f} points vs by hand", delta_color="inverse",
                        help="Stock not turned into pieces (offcuts, saw kerf, trim) as a share of the stock used.")
            proven = bool(c["Proven best"].all())
            m[3].metric("Best possible?", "Yes, proven" if proven else "Best found",
                        help="Proven = equal to a lower bound: no plan can cost less. Otherwise the best plan found "
                             "in the time; the bound is shown per material below.")
            st.caption("*By hand* = what a careful cutter does: longest pieces first, each into the first bar that "
                       "still has room (for sheets: tallest panels first, strip by strip). *As listed* = the same "
                       "but in the order of the list.")
            g1, g2 = st.columns([3, 2])
            with g1:
                fig = go.Figure()
                for lab, col, key in (("as listed", C_LIST, "As listed cost"), ("by hand", C_HAND, "By hand cost"),
                                      ("this plan", C_PLAN, "Planned cost")):
                    fig.add_trace(go.Bar(y=c["Material"], x=c[key], name=lab, orientation="h",
                                         marker=dict(color=col, cornerradius=4),
                                         text=[money(v) for v in c[key]], textposition="outside",
                                         hovertemplate="%{y}: %{x:,.2f}<extra>" + lab + "</extra>"))
                layout(fig, 120 + 70 * len(c), title="Material cost per material", barmode="group")
                fig.update_yaxes(autorange="reversed")
                fig.update_xaxes(range=[0, float(c[["As listed cost", "By hand cost", "Planned cost"]].max().max()) * 1.2])
                st.plotly_chart(fig, width="stretch")
            with g2:
                st.markdown("**Stock to take**")
                st.dataframe(report.stock_to_take(res), hide_index=True, width="stretch",
                             column_config={"Cost each": st.column_config.NumberColumn(format="%.2f"),
                                            "Cost": st.column_config.NumberColumn(format="%.2f")})
            show = c[["Material", "Planned", "By hand", "As listed", "Planned cost", "By hand cost",
                      "Planned waste %", "By hand waste %", "Lower bound", "Proven best"]].copy()
            show.columns = ["Material", f"{u} (plan)", f"{u} (by hand)", f"{u} (as listed)", "cost (plan)",
                            "cost (by hand)", "left over % (plan)", "left over % (by hand)",
                            "no plan costs less than", "proven best"]
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={k: st.column_config.NumberColumn(format="%.1f")
                                        for k in ("left over % (plan)", "left over % (by hand)")})
            st.caption(f"Planned in {res.seconds:.1f} s.")

# ---------------------------------------------------------------------------
# 3. Cutting diagrams
# ---------------------------------------------------------------------------
with tabs[2]:
    if not plan_ok():
        st.info("Plan the cuts first (⚙️ Plan).")
    else:
        res, job = ss.res, ss.job
        d1, d2, _ = st.columns([1, 1, 2])
        pdf_bytes, xlsx_bytes = files_for(res)
        d1.download_button("🖨️ Cut sheets (PDF)", pdf_bytes, "cut_sheets.pdf", "application/pdf",
                           type="primary", width="stretch")
        d2.download_button("📋 Cut list (Excel)", xlsx_bytes, "cut_list.xlsx", XLSX, width="stretch")
        for m, (e, p, *_x) in res.parts.items():
            st.divider()
            n_items = len(jobs.items_of(p))
            pats = p.patterns()
            st.markdown(f"#### {m}")
            st.caption(f"{n_items} {unit_word(job, n_items)} · {len(pats)} different pattern(s) · cut each pattern "
                       "as many times as shown.")
            if isinstance(e, B.BarJob):
                st.caption("Blue: pieces, longest first, a saw kerf between them. Green: offcut to keep. "
                           "Grey: scrap.")
                st.plotly_chart(report.bar_figure(e, p), width="stretch", key=f"bars_{m}")
            else:
                cols = st.columns(2)
                for k, (L, s, n, lay) in enumerate(report.sheet_patterns(e, p)):
                    with cols[k % 2]:
                        st.markdown(f"**{L}** · cut **{n}** × {e.sheets[s].name}")
                        st.plotly_chart(report.sheet_figure(e, s, lay, width=530), width="content",
                                        key=f"sheet_{m}_{L}")
                        st.caption(report.strip_cuts_text(e, lay))
            with st.expander(f"Cut list: {m}"):
                cl = report.cut_list(res)
                st.dataframe(cl[cl["Material"] == m].drop(columns=["Material"]), hide_index=True, width="stretch")
        if job.mode == "bars":
            st.divider()
            st.markdown("#### ♻️ Offcuts to keep")
            oc = report.offcuts(res)
            if oc.empty:
                st.caption(f"No offcut of {job.min_offcut:,.0f} mm or more is left."
                           if job.min_offcut > 0 else "Keeping offcuts is off (📥 Job → Keep offcuts from).")
            else:
                o1, o2 = st.columns([2, 3])
                o1.dataframe(oc, hide_index=True, width="stretch")
                o2.caption("Label them and put them in the rack. They can be stock for the next job, free of cost, "
                           "and the planner uses them before new bars when they fit.")
                if o2.button("Add these offcuts to the stock"):
                    load(jobs.add_offcuts(job, res))
                    st.rerun()

# ---------------------------------------------------------------------------
# 4. Savings
# ---------------------------------------------------------------------------
with tabs[3]:
    if not plan_ok():
        st.info("Plan the cuts first (⚙️ Plan).")
    else:
        res, job = ss.res, ss.job
        sv = report.savings(res)
        c = sv["table"]
        saved, saved_list = sv["by_hand"] - sv["planned"], sv["as_listed"] - sv["planned"]
        pct = saved / sv["by_hand"] if sv["by_hand"] else 0.0
        stock_unit = c["Stock unit"].iloc[0]
        stock_saved = float(c["By hand stock"].sum() - c["Planned stock"].sum())
        m = st.columns(4)
        m[0].metric("Saved on this job", money(saved), delta=f"{pct:.1%} of the material cost by hand")
        m[1].metric("Material saved", f"{stock_saved:,.1f} {stock_unit}",
                    delta=f"{sv['by_hand_n'] - sv['planned_n']} {unit_word(job, sv['by_hand_n'] - sv['planned_n'])}"
                          " fewer")
        per = m[2].number_input("Similar jobs a month", 1, 500, key="per_month")
        m[3].metric("Saved in a year", money(saved * per * 12),
                    delta=f"{stock_saved * per * 12:,.0f} {stock_unit} of material")
        st.caption(f"Compared with a careful cutter (longest pieces first). Compared with cutting in the order of the "
                   f"list, this job saves {money(saved_list)}.")
        g1, g2 = st.columns(2)
        with g1:
            fig = go.Figure()
            labs = ["as listed", "by hand", "this plan"]
            vals = [sv["as_listed"], sv["by_hand"], sv["planned"]]
            fig.add_trace(go.Bar(x=labs, y=vals, marker=dict(color=[C_LIST, C_HAND, C_PLAN], cornerradius=4),
                                 text=[money(v) for v in vals], textposition="outside", showlegend=False,
                                 hovertemplate="%{x}: %{y:,.2f}<extra></extra>"))
            layout(fig, 300, title="Material cost of this job")
            fig.update_yaxes(range=[0, max(vals) * 1.18])
            st.plotly_chart(fig, width="stretch")
        with g2:
            fig = go.Figure()
            for lab, col, key in (("as listed", C_LIST, "As listed waste %"), ("by hand", C_HAND, "By hand waste %"),
                                  ("this plan", C_PLAN, "Planned waste %")):
                fig.add_trace(go.Bar(x=c["Material"], y=c[key], name=lab, marker=dict(color=col, cornerradius=4),
                                     text=[f"{v:.1f}%" for v in c[key]], textposition="outside",
                                     hovertemplate="%{x}: %{y:.1f}%<extra>" + lab + "</extra>"))
            layout(fig, 300, title="Left over (offcuts, kerf and trim), % of the stock used", barmode="group")
            fig.update_yaxes(range=[0, float(c[["As listed waste %", "By hand waste %", "Planned waste %"]].max().max()) * 1.25 + 1])
            st.plotly_chart(fig, width="stretch")
        st.markdown("**Side by side: by hand and this plan**")
        mats = list(res.parts)
        pick = st.selectbox("Material", mats, label_visibility="collapsed") if len(mats) > 1 else mats[0]
        e, p, hand, _listed = res.parts[pick]
        hp = as_plan(e, hand)
        l1, l2 = st.columns(2)
        n_h, n_p = len(hand or []), len(jobs.items_of(p))
        l1.markdown(f"By hand ({'longest' if job.mode == 'bars' else 'tallest'} first): "
                    f"**{n_h} {unit_word(job, n_h)}**")
        l2.markdown(f"This plan: **{n_p} {unit_word(job, n_p)}**")
        if isinstance(e, B.BarJob):
            l1.plotly_chart(report.bar_figure(e, hp, height_per=34), width="stretch", key="hand_bars")
            l2.plotly_chart(report.bar_figure(e, p, height_per=34), width="stretch", key="plan_bars")
        else:
            for col, plan_, tag in ((l1, hp, "hand"), (l2, p, "plan")):
                for k, (L, s, n, lay) in enumerate(report.sheet_patterns(e, plan_)):
                    col.caption(f"{L} · ×{n}")
                    col.plotly_chart(report.sheet_figure(e, s, lay, width=530), width="content",
                                     key=f"side_{tag}_{k}")

# ---------------------------------------------------------------------------
# 5. Benchmark
# ---------------------------------------------------------------------------
with tabs[4]:
    rb, rs = results_file("bars"), results_file("sheets")
    if rb is None and rs is None:
        st.info("No benchmark results yet: run `python benchmarks/run_benchmark.py bars` (and `sheets`).")
    if rb is not None:
        df = pd.DataFrame(rb["instances"])
        df["gap"] = df["bins"] - df["optimal"]
        st.markdown("#### Bars: 1,615 instances with proven optimal answers")
        st.caption(f"BPPLIB (Delorme, Iori & Martello): every instance has a known optimal number of bars. "
                   f"{rb['seconds_per_instance']:.0f} s per instance, {rb['machine']}.")
        m = st.columns(5)
        m[0].metric("Instances", f"{len(df):,}")
        m[1].metric("Optimum matched", f"{(df['gap'] == 0).sum():,}", delta=f"{(df['gap'] == 0).mean():.1%}",
                    delta_color="off")
        m[2].metric("Within one bar", f"{(df['gap'] <= 1).mean():.1%}")
        m[3].metric("First fit decreasing matched", f"{(df['ffd'] == df['optimal']).mean():.1%}",
                    help="Longest piece first, into the first bar with room: the careful cutter by hand.")
        m[4].metric("Plans valid (checker)", f"{int(df['valid'].sum()):,}")
        fam = df.groupby("family").agg(instances=("name", "size"), matched=("gap", lambda g: int((g == 0).sum())),
                                       plus_one=("gap", lambda g: int((g == 1).sum())),
                                       more=("gap", lambda g: int((g > 1).sum())),
                                       ffd=("ffd", "sum"), plan=("bins", "sum"), optimal=("optimal", "sum"),
                                       seconds=("seconds", "mean")).reset_index()
        fam["ffd_matched"] = df.assign(ok=df["ffd"] == df["optimal"]).groupby("family")["ok"].sum().values
        g1, g2 = st.columns([3, 2])
        with g1:
            fig = go.Figure()
            fig.add_trace(go.Bar(y=fam["family"], x=100 * fam["ffd_matched"] / fam["instances"], orientation="h",
                                 name="by hand (first fit decreasing)", marker=dict(color=C_HAND, cornerradius=4),
                                 text=[f"{v:.0f}%" for v in 100 * fam["ffd_matched"] / fam["instances"]],
                                 textposition="outside"))
            fig.add_trace(go.Bar(y=fam["family"], x=100 * fam["matched"] / fam["instances"], orientation="h",
                                 name="Cut Planner", marker=dict(color=C_PLAN, cornerradius=4),
                                 text=[f"{v:.0f}%" for v in 100 * fam["matched"] / fam["instances"]],
                                 textposition="outside"))
            layout(fig, 330, title="Instances solved to the proven optimum, by family", barmode="group")
            fig.update_xaxes(range=[0, 115], ticksuffix="%")
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, width="stretch")
        with g2:
            show = fam[["family", "instances", "matched", "plus_one", "more", "seconds"]].copy()
            show.columns = ["family", "instances", "optimum", "+1 bar", "+2 or more", "avg s"]
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={"avg s": st.column_config.NumberColumn(format="%.1f")})
            st.caption(f"Bars over all instances: plan {int(df['bins'].sum()):,}, optimum {int(df['optimal'].sum()):,}, "
                       f"first fit decreasing {int(df['ffd'].sum()):,}.")
        with st.expander("Instances not solved to the optimum"):
            st.dataframe(df[df["gap"] > 0][["family", "name", "items", "capacity", "bins", "optimal", "ffd",
                                            "seconds"]], hide_index=True, width="stretch")
    if rs is not None:
        st.divider()
        dfs = pd.DataFrame(rs["instances"])
        st.markdown("#### Sheets: 500 CLASS instances")
        st.caption("Berkey & Wang (1987) and Martello & Vigo (1998): ten classes of random rectangles, panels may "
                   "not turn. The optimum is not known for every instance here, so plans are compared with a lower "
                   f"bound (no plan can use fewer sheets) and with cutting by hand strip by strip. "
                   f"{rs['seconds_per_instance']:.0f} s per instance, {rs['machine']}.")
        m = st.columns(5)
        m[0].metric("Instances", len(dfs))
        eq = int((dfs["sheets"] == dfs["bound"]).sum())
        m[1].metric("Proven optimal (= bound)", eq, delta=f"{eq / len(dfs):.0%}", delta_color="off")
        m[2].metric("Sheets used", f"{int(dfs['sheets'].sum()):,}",
                    delta=f"{int(dfs['sheets'].sum() - dfs['shelf'].sum()):,} vs by hand", delta_color="inverse")
        m[3].metric("Lower bound", f"{int(dfs['bound'].sum()):,}",
                    help="Area of the panels, and panels too large to share a sheet: no plan can use fewer.")
        m[4].metric("Plans valid (checker)", f"{int(dfs['valid'].sum())}")
        st.caption(f"Fewer sheets than by hand on {int((dfs['sheets'] < dfs['shelf']).sum())} instances, more on "
                   f"{int((dfs['sheets'] > dfs['shelf']).sum())}.")
        cl = dfs.groupby("class").agg(plan=("sheets", "sum"), hand=("shelf", "sum"), bound=("bound", "sum")).reset_index()
        fig = go.Figure()
        for lab, col, key in (("by hand (strips)", C_HAND, "hand"), ("Cut Planner", C_PLAN, "plan")):
            fig.add_trace(go.Bar(x=[f"class {k}" for k in cl["class"]], y=100 * (cl[key] / cl["bound"] - 1),
                                 name=lab, marker=dict(color=col, cornerradius=4),
                                 hovertemplate="%{x}: %{y:.1f}% above the bound<extra>" + lab + "</extra>"))
        layout(fig, 300, title="Sheets above the lower bound, per class (lower is better)", barmode="group")
        fig.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# 6. How it works
# ---------------------------------------------------------------------------
with tabs[5]:
    st.markdown("""
#### The problem
A workshop cuts pieces from stock: window profiles from 6 m bars, cabinet panels from 2440 × 1220 boards. Which
pieces go on which bar or sheet decides how much stock is bought and how much ends up as scrap. By hand, a cutter
takes the longest piece first and puts it on the first bar with room: quick, but on a real order it often needs a bar
or a sheet more than necessary.

#### Bars (one dimension)
- **Patterns.** A pattern is one way to cut a bar (which pieces, with a saw kerf at every cut and the trim). The
  planner looks for the cheapest mix of patterns that gives every piece.
- **Column generation** (Gilmore & Gomory, 1961). A linear program (HiGHS) decides how often to use each known
  pattern and puts a price on every piece; a knapsack (dynamic programming) then finds the pattern that is worth most
  at those prices. When no pattern helps, the linear program's value is a **lower bound**: no plan can cost less.
- **Integer plan.** Diving (fix the patterns the linear program uses most, solve the rest again, randomised
  repeats) and CP-SAT (Google OR-Tools) over all patterns found. When the plan costs as much as the bound, it is
  **proven best**.
- **Offcuts.** Offcuts from the rack are stock with cost 0 and a count; leftovers above your threshold are listed to
  keep.

#### Sheets (two dimensions)
- **Guillotine cuts**, the way a panel saw or a glass cutter works: rip the sheet into strips, cut each strip into
  columns, cut each column into the panels stacked in it. Both directions of the sheet are tried.
- **Patterns** come from a three-level knapsack (best column for each width, best strip for each height, best stack
  of strips), priced by column generation, plus *sequential value correction*: fill sheets one at a time and give
  awkward panels more value next round.
- **Grain.** A panel with grain keeps its length along the sheet's length; others may turn.
- **Lower bound** from the panels' area and from panels too big to share a sheet.

#### Checked independently
Every plan is re-checked by separate code: every piece cut exactly as ordered, every bar within its length with
kerf and trim, every panel inside the trimmed sheet with kerf between panels, grain kept, no stock used beyond what is
available, and every sheet cuttable with straight edge-to-edge cuts (a recursive guillotine test).

#### Limits
- Square cuts on bars: mitred ends are planned at their full length.
- Sheets: staged guillotine patterns (strips, columns, stacks); no free nesting of shapes.
- Prices and offcut rules are yours to set; the demo prices are made up.

#### References
P. C. Gilmore & R. E. Gomory (1961), *A linear programming approach to the cutting-stock problem*.
M. Delorme, M. Iori & S. Martello (2018), *BPPLIB: a library for bin packing and cutting stock problems*.
J. O. Berkey & P. Y. Wang (1987); S. Martello & D. Vigo (1998): two-dimensional bin packing instances (CLASS).
""")

# ---------------------------------------------------------------------------
# sidebar: the job and what the plan saves (filled last, after any planning in this run)
# ---------------------------------------------------------------------------
with side:
    if ss.job is not None:
        st.markdown(f"**{ss.job.name}**")
        st.caption(f"{len(ss.job.pieces)} piece rows · {len(ss.job.materials())} material(s) · "
                   f"{'bars' if ss.job.mode == 'bars' else 'sheets'}")
        if plan_ok():
            sv = report.savings(ss.res)
            st.metric("Saved vs by hand", money(sv["by_hand"] - sv["planned"]),
                      delta=f"{sv['by_hand_n'] - sv['planned_n']} {unit_word(ss.job, 2)} fewer")
