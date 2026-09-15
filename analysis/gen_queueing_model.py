#!/usr/bin/env python3
"""
XRAY paper — M/G/1-with-server-vacations queueing model (stress-window-only).
See xray_stress_window.py for the shared stress-round isolation methodology
(matches gen_figures_n30.py's approach, used consistently for Section 5/6
and this analysis).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from xray_stress_window import CONDITIONS, find_run_dir, good_reps, stress_pauses, latencies, moments
from finalize_anchor_pool import BESU_SOURCES, NM_SOURCES, reps_for
import statistics as st


def lat_stats(lat):
    s = sorted(lat)
    n = len(s)
    return dict(n=n, mean=st.mean(s), std=st.stdev(s), cv=st.stdev(s) / st.mean(s),
                p50=s[int(n * 0.5)], p90=s[int(n * 0.9)], p99=s[int(n * 0.99)], max=s[-1])


def _anchor_pools(platform):
    """Critique fix (Option A): pool GC pauses across every anchor_lat run
    directory (matches gen_ofat_summary.py's anchor pooling, so Table 3,
    Table 4's anchor row, and this queueing anchor row are all consistent).
    Latency is pooled only from sources that have per-tx latency CSVs
    (the old June baseline predates latency tracing)."""
    sources = BESU_SOURCES if platform == 'besu' else NM_SOURCES
    pauses, lat = [], []
    # n180 completeness is judged on the COMBINED good_reps count across
    # every 'n180'-labeled source (see finalize_anchor_pool.py's docstring
    # for the host-effect finding that led to a split-leg, single-host
    # rerun), not on any single source's own directory reaching 180.
    n180_total_done = 0
    for src_label, run_dir, _ in sources:
        if 'n180' in src_label:
            gr = os.path.join(run_dir, 'good_reps.txt')
            n180_total_done += len(open(gr).read().split()) if os.path.exists(gr) else 0
    for src_label, run_dir, rep_names in sources:
        if 'n180' in src_label:
            if n180_total_done < 180:
                continue
        reps = reps_for(run_dir, rep_names)
        if not reps:
            continue
        for rep in reps:
            rep_dir = os.path.join(run_dir, rep)
            events = stress_pauses(platform, rep_dir, 120)
            pauses.extend(p for _, p in events)
        lat.extend(latencies(run_dir, reps))
    return pauses, lat


def analyze(label, platforms=('besu', 'nm')):
    result = {}
    dur_s = CONDITIONS[label][2]
    for platform in platforms:
        if label == 'anchor_lat':
            pauses, lat = _anchor_pools(platform)
            if not pauses:
                print(f'MISSING pooled anchor data: {platform}/{label}', file=sys.stderr)
                continue
            e_v, e_v2, resid = moments(pauses)
            ls = lat_stats(lat)
            total_elapsed_ms = dur_s * (len(lat) or 1) * 1000.0  # not used downstream for anchor
            f_gc = 0.0
            result[platform] = dict(n_events=len(pauses), e_v=e_v, e_v2=e_v2, residual=resid,
                                     f_gc=f_gc, **ls)
            continue
        run_dir = find_run_dir(platform, label)
        if run_dir is None:
            print(f'MISSING run dir: {platform}/{label}', file=sys.stderr)
            continue
        reps = good_reps(run_dir)
        pauses = []
        for rep in reps:
            rep_dir = os.path.join(run_dir, rep)
            events = stress_pauses(platform, rep_dir, dur_s)
            pauses.extend(p for _, p in events)
        lat = latencies(run_dir, reps)
        e_v, e_v2, resid = moments(pauses)
        ls = lat_stats(lat)
        n_reps = len(reps)
        total_elapsed_ms = dur_s * n_reps * 1000.0
        total_pause = sum(pauses)
        f_gc = total_pause / total_elapsed_ms if total_elapsed_ms else 0.0
        result[platform] = dict(n_events=len(pauses), e_v=e_v, e_v2=e_v2, residual=resid,
                                 f_gc=f_gc, **ls)
    return result


if __name__ == '__main__':
    all_results = {}
    for label in CONDITIONS:
        print(f'--- {label} ---', file=sys.stderr)
        all_results[label] = analyze(label)
    with open('/tmp/xray_queueing_model_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    for label, res in all_results.items():
        for platform, d in res.items():
            print(f"{label:24s} {platform:5s} n_events={d['n_events']:4d} E[V]={d['e_v']:7.2f}ms "
                  f"E[V2]={d['e_v2']:9.1f} R={d['residual']:7.2f}ms f_gc={d['f_gc']*100:5.2f}% | "
                  f"lat mean={d['mean']:7.1f} std={d['std']:6.1f} cv={d['cv']:.2f} "
                  f"p50={d['p50']} p90={d['p90']} p99={d['p99']}")
