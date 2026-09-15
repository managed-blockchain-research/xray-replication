#!/usr/bin/env python3
"""
XRAY paper -- unify Table 3 (main-results, Section 5/6) and Table 4
(OFAT anchor row) + Section 8 (queueing anchor row / Fig 10 anchor point)
onto a SINGLE pooled anchor_lat dataset per platform (critique Option A).

Pools every available anchor_lat replication across all known run
directories (old June baseline + OFAT-sweep official n=30 + the
n=180 PAF-stability rerun once it completes), all re-extracted through
the SAME stress-window-isolation methodology (xray_stress_window.py),
so Table 3 and Table 4's anchor row are computed identically.

The n=180 PAF-stability rerun was split across two hosts mid-run
(compute24 got stuck for hours behind another team's CPU-saturating job,
so the remaining reps were relaunched on compute23). A Welch t-test on
the two hosts' total_pause_ms found a statistically significant host
effect (86.70+-13.12ms on compute24, n=48, vs. 96.05+-29.87ms on
compute23, n=97; t=-2.62, df=141) despite matching CPU/RAM/.NET
versions, and compute23's own attempt-index vs. pause correlation was
~0 (not a within-run drift artifact) -- so compute24's partial data was
dropped rather than pooled across a confirmed-heterogeneous pair of
hosts. The n=180 target is met entirely from compute23 (a first leg of
148 reps plus a second leg of the remaining reps, both under this
directory's parent). Every 'n180'-labeled source below is judged for
completeness on their COMBINED good_reps.txt count vs. 180, not on any
one directory individually reaching 180.

The same Welch-t-test standard was applied retroactively to the
old_june source (2026-06-27/28, predating the JDK-pinning and
resource-gate fixes documented in experiments/xray/HANDOFF_compute24.md)
against ofat_official (2026-08-19). Besu: old_june 46.32+-9.96ms
(n=30) vs. ofat_official 42.72+-7.14ms (n=30), t=1.61 -- not
significant, kept. NM: old_june 133.42+-108.24ms (n=30) vs.
ofat_official 83.62+-15.49ms (n=30), t=2.49 -- significant, AND
old_june's variance is ~7x ofat_official's, consistent with noise from
the pre-fix testbed -- dropped. NM_SOURCES therefore has no old_june
entry; BESU_SOURCES keeps it. This means Besu's and NM's anchor pools
are built from different-sized, independently-justified source lists
(60 vs. ofat_official+n180) rather than a symmetric-by-construction set
-- each platform's inclusion list is decided on its own evidence, not
for stylistic symmetry with the other.

Usage: python3 finalize_anchor_pool.py [--include-partial]
  --include-partial: also include the in-progress n=180 run's reps
                      completed so far (for dry-run/preview only).
"""
import glob
import json
import os
import statistics as st
import sys
import csv

sys.path.insert(0, os.path.dirname(__file__))
from xray_stress_window import besu_stress_pauses, nm_stress_pauses, moments

RESULTS = os.path.expanduser('~/banning/experiments/xray/results')
STRESS_DUR = 120

# (label, run_dir, rep_dir_names or None-for-good_reps.txt)
BESU_SOURCES = [
    ('old_june', f'{RESULTS}/besu_n30/20260628_152226_xray_besu_n30',
     [f'besu_{i}' for i in range(1, 31)]),
    ('ofat_official', f'{RESULTS}/sweep/besu/anchor_lat/20260819_174300_anchor_lat', None),
]

NM_SOURCES = [
    # old_june (20260627_000957_xray_nm_clique_n30) dropped -- see module
    # docstring for the Welch t-test finding that it's statistically
    # heterogeneous vs. ofat_official (~7x the variance, pre-fix testbed).
    ('ofat_official', f'{RESULTS}/sweep/nm/anchor_lat/20260819_193201_anchor_lat', None),
    # compute24's n180 leg (20260820_165217_anchor_lat) was dropped -- see
    # module docstring for the Welch t-test host-effect finding. n180 is
    # now sourced entirely from compute23, split across two sequential legs
    # (148 + a remaining-reps follow-up) under separate RUN_ID directories,
    # both labeled 'n180' so the combined-completeness gate below sums them
    # together. Leg 1 (target 148) completed clean: 148/148 good, 0
    # retries/failures. Leg 2 (target 32, to reach 180) is running.
    ('ofat_n180_leg1', f'{RESULTS}/sweep/nm/anchor_lat/20260821_073816_anchor_lat', None),
    ('ofat_n180_leg2', f'{RESULTS}/sweep/nm/anchor_lat/20260821_171228_anchor_lat', None),
]


def reps_for(run_dir, rep_names):
    if rep_names is not None:
        return rep_names
    gr = os.path.join(run_dir, 'good_reps.txt')
    if not os.path.exists(gr):
        return []
    return [l.strip() for l in open(gr) if l.strip()]


def confirmed_tx_count(rep_dir):
    """Count confirmed (success=true) tx from latency_worker*.csv in this rep."""
    total = 0
    for f in glob.glob(os.path.join(rep_dir, 'latency_worker*.csv')):
        with open(f) as fh:
            r = csv.DictReader(fh)
            for row in r:
                if row.get('success') == 'true':
                    total += 1
    if total:
        return total
    # fallback: metrics.txt "succ=" style line, if latency CSVs aren't present
    mpath = os.path.join(rep_dir, 'metrics.txt')
    if os.path.exists(mpath):
        for line in open(mpath):
            if 'succ' in line.lower():
                import re
                m = re.search(r'succ[= ](\d+)', line)
                if m:
                    return int(m.group(1))
    return None


def pool_platform(platform, sources, include_partial):
    per_source = {}
    all_pauses = []       # (gen, pause_ms) across every rep, every source
    per_rep_totals = []   # total_pause per rep (0 if zero events -- real data point)
    per_rep_avgs = []     # per-rep avg pause (only reps with >=1 event)
    per_rep_counts = []   # event count per rep
    tx_counts = []
    n_reps_used = 0

    # n180 completeness is judged on the COMBINED good_reps count across
    # every 'n180'-labeled source (the run may be split across sequential
    # legs, or across hosts -- see module docstring), not on any single
    # source's own directory reaching 180 in isolation.
    n180_total_done = 0
    for label, run_dir, _ in sources:
        if 'n180' in label:
            gr = os.path.join(run_dir, 'good_reps.txt')
            n180_total_done += len(open(gr).read().split()) if os.path.exists(gr) else 0

    for label, run_dir, rep_names in sources:
        if 'n180' in label:
            if n180_total_done < 180 and not include_partial:
                print(f'  [skip, only {n180_total_done}/180 done combined across n180 sources] '
                      f'{label}: {run_dir}', file=sys.stderr)
                continue
        reps = reps_for(run_dir, rep_names)
        if not reps:
            print(f'  [skip, no reps found] {label}: {run_dir}', file=sys.stderr)
            continue
        src_pauses = []
        src_reps_used = 0
        for rep in reps:
            rep_dir = os.path.join(run_dir, rep)
            if platform == 'besu':
                events = besu_stress_pauses(rep_dir, STRESS_DUR)
            else:
                events = nm_stress_pauses(rep_dir, STRESS_DUR)
            if events is None:
                continue
            pauses = [p for _, p in events]
            total = sum(pauses)
            per_rep_totals.append(total)
            per_rep_counts.append(len(pauses))
            if pauses:
                per_rep_avgs.append(total / len(pauses))
            all_pauses.extend(pauses)
            src_pauses.extend(pauses)
            src_reps_used += 1
            tc = confirmed_tx_count(rep_dir)
            if tc:
                tx_counts.append(tc)
        n_reps_used += src_reps_used
        per_source[label] = dict(
            n_reps=src_reps_used,
            n_events=len(src_pauses),
            total_pause_mean=(sum(src_pauses) / src_reps_used) if src_reps_used else None,
            avg_pause_pooled=(st.mean(src_pauses) if src_pauses else None),
        )
        print(f'  {label}: n={src_reps_used} events={len(src_pauses)} '
              f'avg_pause_pooled={st.mean(src_pauses) if src_pauses else float("nan"):.2f}ms',
              file=sys.stderr)

    e1, e2, R = moments(all_pauses)
    result = dict(
        platform=platform,
        n_reps=n_reps_used,
        n_events=len(all_pauses),
        total_pause_mean=st.mean(per_rep_totals) if per_rep_totals else None,
        total_pause_std=st.stdev(per_rep_totals) if len(per_rep_totals) > 1 else 0.0,
        avg_pause_pooled=e1,
        avg_pause_per_rep_mean=st.mean(per_rep_avgs) if per_rep_avgs else None,
        avg_pause_per_rep_std=st.stdev(per_rep_avgs) if len(per_rep_avgs) > 1 else 0.0,
        pause_std_pooled=st.stdev(all_pauses) if len(all_pauses) > 1 else 0.0,
        residual_R=R,
        events_per_rep_mean=st.mean(per_rep_counts) if per_rep_counts else None,
        tx_count_mean=st.mean(tx_counts) if tx_counts else None,
        per_source=per_source,
    )
    return result


def main():
    include_partial = '--include-partial' in sys.argv
    print('=== Besu anchor_lat pool ===', file=sys.stderr)
    besu = pool_platform('besu', BESU_SOURCES, include_partial)
    print('=== Nethermind anchor_lat pool ===', file=sys.stderr)
    nm = pool_platform('nm', NM_SOURCES, include_partial)

    paf = (nm['avg_pause_pooled'] / besu['avg_pause_pooled']) if besu['avg_pause_pooled'] else None
    tau_besu = (besu['total_pause_mean'] / besu['tx_count_mean']) if besu.get('tx_count_mean') else None
    tau_nm = (nm['total_pause_mean'] / nm['tx_count_mean']) if nm.get('tx_count_mean') else None
    tau_ratio = (tau_nm / tau_besu) if (tau_besu and tau_nm) else None

    out = dict(besu=besu, nm=nm, paf_pooled_avg=paf,
               tau_tx_besu=tau_besu, tau_tx_nm=tau_nm, tau_ratio=tau_ratio)
    print(json.dumps(out, indent=2))
    with open('/tmp/xray_anchor_pool_result.json', 'w') as f:
        json.dump(out, f, indent=2)
    print('\nsaved /tmp/xray_anchor_pool_result.json', file=sys.stderr)
    print(f"\nBesu:  n={besu['n_reps']} total_pause={besu['total_pause_mean']:.1f}+-{besu['total_pause_std']:.1f}ms "
          f"avg_pause(pooled)={besu['avg_pause_pooled']:.2f}ms  R={besu['residual_R']:.2f}", file=sys.stderr)
    print(f"NM:    n={nm['n_reps']} total_pause={nm['total_pause_mean']:.1f}+-{nm['total_pause_std']:.1f}ms "
          f"avg_pause(pooled)={nm['avg_pause_pooled']:.2f}ms  R={nm['residual_R']:.2f}", file=sys.stderr)
    if paf:
        print(f"PAF (pooled avg ratio) = {paf:.2f}x", file=sys.stderr)
    if tau_ratio:
        print(f"tau_tx ratio = {tau_ratio:.2f}x  (besu={tau_besu:.4f} nm={tau_nm:.4f} ms/tx)", file=sys.stderr)


if __name__ == '__main__':
    main()
