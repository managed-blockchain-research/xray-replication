# XRAY: Cross-Runtime Analysis

Replication package for XRAY cross-runtime diagnostic evaluation.

## Structure
- `configs/` — Caliper benchmark configurations
- `scripts/` — Run scripts (launch clients, drive the Caliper workload, collect GC traces)
- `analysis/` — Post-processing scripts that turn raw per-replication GC/latency
  logs into the paper's figures and tables: `finalize_anchor_pool.py` and
  `xray_stress_window.py` (stress-window pause extraction shared by the other
  scripts), `gen_figures_n30.py` (anchor-condition Figures 2–6, Table 2),
  `gen_ofat_summary.py` (OFAT sensitivity sweep, Figures 7–9, Table 3),
  `gen_queueing_model.py` / `gen_queueing_figures.py` (M/G/1-with-vacations
  residual-vacation diagnostic, Figure 10, Table 4), and
  `compute_narrative_numbers.py` (cross-checks the numeric claims quoted in
  the paper text against the underlying data). These scripts reference
  absolute paths from the original experiment host; update the path
  constants near the top of each file to point at your own results
  directory.
  `analysis/ofat_summary.json` and `analysis/queueing_model_results.json`
  are the pre-computed outputs of `gen_ofat_summary.py` and
  `gen_queueing_model.py` respectively, run against the full raw
  per-replication logs (which are too large to host here) — they
  reproduce Tables 3 and 4 of the paper exactly and let you check the
  paper's numbers without re-running the sweep yourself.
- `results/` — GC event and latency summary CSVs
- `src/` — Smart contract source
