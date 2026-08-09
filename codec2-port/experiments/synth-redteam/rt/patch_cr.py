"""Re-run cr-rt steady rows after the Nt-bracket fix; merge into steady_rt.csv."""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_redteam as rr

cr = ["cr-rt-full", "cr-rt-inc", "cr-rt-inc-1db", "cr-rt-inc-m2", "cr-rt-nn"]
new_rows = rr.steady_grid(cr, ["aa", "iy", "uw"], rr.F0_GRID, "std",
                          "steady_cr_fix.csv")
path = os.path.join(rr.RESULTS, "steady_rt.csv")
rows = [r for r in csv.DictReader(open(path)) if r["engine"] not in cr]
allrows = rows + [{k: str(v) for k, v in r.items()} for r in new_rows]
with open(path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(allrows[0].keys()))
    w.writeheader()
    w.writerows(allrows)
conv = [{**r,
         "amp_shape_mean_db": float(r["amp_shape_mean_db"]),
         "amp_shape_max_db": float(r["amp_shape_max_db"]),
         "spur_db": float(r["spur_db"]),
         "nmr_proxy_db": float(r["nmr_proxy_db"])} for r in allrows]
agg = rr.agg_rows(conv, rr.CONTROLS + rr.DEFENDERS + rr.WAVE2)
json.dump(agg, open(os.path.join(rr.RESULTS, "steady_rt_aggregate.json"), "w"),
          indent=1)
for k in ["cycle-replay", "cycle-replay-2x", "cr-rt-full", "cr-rt-inc",
          "cr-rt-inc-m2", "cr-rt-nn"]:
    v = agg[k]
    print(f"{k:18s} env {v['env_mean_db']:5.2f}/{v['env_max_db']:6.2f} "
          f"spur {v['spur_worst_db']:7.1f} nmr {v['nmr_worst_db']:7.1f}")
