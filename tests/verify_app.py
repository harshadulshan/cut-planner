"""
App checks: clicks through every tab of the Streamlit app (Streamlit's AppTest, no browser): both demos, planning,
the independent check, savings, diagrams and downloads, offcuts back into stock, a job with mistakes, a changed job.

    python tests/verify_app.py     # about 2 minutes
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

passed, failed = 0, []


def ok(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


def button(at, label):
    return [b for b in at.button if b.label == label][0]


def metric(at, label):
    m = [m for m in at.metric if m.label == label]
    return m[0].value if m else None


def errors(at):
    return [e.value for e in at.exception]


at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=600)
at.run()
ok("app starts without errors", not errors(at), str(errors(at)))
ok("six tabs", [t.label for t in at.tabs][:6] == ["📥 Job", "⚙️ Plan", "✂️ Cutting diagrams", "💰 Savings",
                                                  "🧪 Benchmark", "ℹ️ How it works"])
ok("before a job: ask to load one", any("Load a demo" in i.value for i in at.info))

button(at, "Load the window demo").click().run()
ok("window demo loads: 132 pieces, 2 materials", metric(at, "Pieces") == "132" and metric(at, "Materials") == "2",
   str([(m.label, m.value) for m in at.metric]))
at.slider[0].set_value(8).run()
button(at, "Plan the cuts").click().run()
ok("window demo planned without errors", not errors(at), str(errors(at)))
ok("independent check passed", any("Independent check passed" in s.value for s in at.success))
ok("29 bars, $980, proven best", metric(at, "Bars used") == "29" and metric(at, "Material cost") == "$980" and
   metric(at, "Best possible?") == "Yes, proven", str([(m.label, m.value) for m in at.metric]))
ok("savings: $77 on this job", metric(at, "Saved on this job") == "$77")
at.number_input(key="per_month").set_value(10).run()
ok("a year at 10 jobs a month: $9,240", metric(at, "Saved in a year") == "$9,240")
labels = [d.label for d in at.get("download_button")]
ok("downloads: cut sheets PDF and cut list Excel", "🖨️ Cut sheets (PDF)" in labels and "📋 Cut list (Excel)" in labels,
   str(labels))
ok("offcuts to keep are listed", any(df.value.shape[1] == 3 and "Length" in df.value.columns for df in at.dataframe))
n_stock = len(at.session_state["job"].stock)
button(at, "Add these offcuts to the stock").click().run()
ok("offcuts go back into the stock (free)", len(at.session_state["job"].stock) > n_stock and
   at.session_state["res"] is None)
button(at, "Plan the cuts").click().run()
ok("planned again with the new offcuts: not dearer", not errors(at) and
   float(metric(at, "Material cost").replace("$", "").replace(",", "")) <= 980)

button(at, "Load the kitchen demo").click().run()
ok("kitchen demo loads: 90 panels", metric(at, "Pieces") == "90")
button(at, "Plan the cuts").click().run()
ok("kitchen planned: 9 sheets, $510, check passed", metric(at, "Sheets used") == "9" and
   metric(at, "Material cost") == "$510" and any("Independent check passed" in s.value for s in at.success),
   str([(m.label, m.value) for m in at.metric]))
ok("savings vs by hand: $48", metric(at, "Saved on this job") == "$48")
ok("cutting diagrams drawn for every sheet pattern",
   len([c for c in at.get("plotly_chart")]) >= 9, str(len(at.get("plotly_chart"))))
at.number_input(key="kerf_" + str(at.session_state["version"])).set_value(6.0).run()
ok("a changed job asks to plan again", any("changed since this plan" in w.value for w in at.warning))

button(at, "Load the kitchen demo").click().run()
job = at.session_state["job"]
job.pieces.loc[0, "Material"] = "Glass 6 mm"
at.session_state["job"] = job
at.session_state["version"] += 1
at.run()
button(at, "Plan the cuts").click().run()
ok("a job with a mistake is explained, not planned", any("Glass 6 mm" in e.value for e in at.error) and
   not errors(at), str([e.value for e in at.error]))

at.run()
ok("benchmark tab renders", not errors(at))
ok("how it works tab explains the method", any("Column generation" in m.value for m in at.markdown))

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
