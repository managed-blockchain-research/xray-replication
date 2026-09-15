#!/usr/bin/env python3
"""
XRAY paper — OFAT sensitivity sweep summary (stress-window-only GC pauses).
Recomputes GC pause statistics directly from raw gc.log / gc_trace.nettrace,
isolated to the stress round only (matching gen_figures_n30.py's original
methodology for Section 5/6), for methodological consistency across the
whole paper. TPS/fail-rate are read from the sweep's own aggregate_summary.json
(already stress-round-specific, computed by the sweep script itself).
"""
import json
import os
import statistics as st
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from xray_stress_window import CONDITIONS, find_run_dir, good_reps, stress_pauses
from finalize_anchor_pool import BESU_SOURCES, NM_SOURCES, reps_for

OUT = os.path.expanduser('~/banning/papers/xray/')

plt.rcParams.update({
    'font.family': 'serif', 'font.size': 11, 'axes.labelsize': 12,
    'legend.fontsize': 10, 'xtick.labelsize': 10, 'ytick.labelsize': 10,
    'svg.fonttype': 'none',
})

BLUE = '#2166AC'
ORANGE = '#D6604D'


def per_rep_stats(platform, run_dir, rep, dur_s):
    """Returns per-rep stats, or None only if the stress window itself could
    not be correlated (missing caliper/gc log) — NOT when a rep legitimately
    has zero GC events during its stress window (that's total_pause=0, a
    real data point, not a missing one)."""
    rep_dir = os.path.join(run_dir, rep)
    if platform == 'besu':
        if not os.path.exists(os.path.join(rep_dir, 'gc.log')):
            return None
    else:
        if not os.path.exists(os.path.join(rep_dir, 'gc_trace.nettrace')):
            return None
    if not os.path.exists(os.path.join(rep_dir, 'caliper_console.log')):
        return None
    events = stress_pauses(platform, rep_dir, dur_s)
    pauses = [p for _, p in events]
    if platform == 'besu':
        gen0 = sum(1 for g, _ in events if g == 'young')
        full = sum(1 for g, _ in events if g == 'full')
    else:
        gen0 = sum(1 for g, _ in events if g == 0)
        full = sum(1 for g, _ in events if g == 2)  # gen2 as "full" analog
    total_pause = sum(pauses)
    avg_pause = (total_pause / len(pauses)) if pauses else None
    return dict(gen0=gen0, full=full, total=len(pauses), total_pause=total_pause, avg_pause=avg_pause)


def load_combo(platform, label):
    dur_s = CONDITIONS[label][2]
    per_rep = []
    tps_weighted_sum = fail_weighted_sum = weight_total = 0.0
    if label == 'anchor_lat':
        # Critique fix (Option A): pool ALL available anchor_lat replications
        # across every known run directory (old June baseline + OFAT-sweep
        # official n=30 + n=180 PAF-stability rerun) through this SAME
        # stress-window-isolation pipeline, so Table 3 (Section 5/6) and
        # this table's anchor row are computed identically -- not two
        # independently-sampled n=30 draws from a heavy-tailed distribution.
        sources = BESU_SOURCES if platform == 'besu' else NM_SOURCES
        # n180 completeness is judged on the COMBINED good_reps count across
        # every 'n180'-labeled source (the rerun may be split across
        # sequential legs or hosts -- see finalize_anchor_pool.py's
        # docstring for the host-effect finding that motivated this), not
        # on any single source's own directory reaching 180 in isolation.
        n180_total_done = 0
        for src_label, run_dir, _ in sources:
            if 'n180' in src_label:
                gr = os.path.join(run_dir, 'good_reps.txt')
                n180_total_done += len(open(gr).read().split()) if os.path.exists(gr) else 0
        for src_label, run_dir, rep_names in sources:
            if 'n180' in src_label:
                if n180_total_done < 180:
                    continue  # skip incomplete rerun
            reps = reps_for(run_dir, rep_names)
            if not reps:
                continue
            src_per_rep = [per_rep_stats(platform, run_dir, r, dur_s) for r in reps]
            src_per_rep = [p for p in src_per_rep if p is not None]
            per_rep.extend(src_per_rep)
            agg_path = os.path.join(run_dir, 'aggregate_summary.json')
            if os.path.exists(agg_path) and src_per_rep:
                with open(agg_path) as f:
                    a = json.load(f)
                w = len(src_per_rep)
                tps_weighted_sum += a['tps_mean'] * w
                fail_weighted_sum += a['fail_mean'] * w
                weight_total += w
        if not per_rep:
            return None
        tps_mean = (tps_weighted_sum / weight_total) if weight_total else None
        fail_mean = (fail_weighted_sum / weight_total) if weight_total else None
        return _aggregate(per_rep, tps_mean, fail_mean)

    run_dir = find_run_dir(platform, label)
    if run_dir is None:
        return None
    reps = good_reps(run_dir)
    per_rep = [per_rep_stats(platform, run_dir, r, dur_s) for r in reps]
    per_rep = [p for p in per_rep if p is not None]
    if not per_rep:
        return None
    agg_path_candidates = [os.path.join(run_dir, 'aggregate_summary.json')]
    tps_mean = fail_mean = None
    for p in agg_path_candidates:
        if os.path.exists(p):
            with open(p) as f:
                a = json.load(f)
            tps_mean, fail_mean = a['tps_mean'], a['fail_mean']
    return _aggregate(per_rep, tps_mean, fail_mean)


def _aggregate(per_rep, tps_mean, fail_mean):
    # total_pause includes every rep (0 for a rep with no stress-window GC
    # event — a real outcome); avg_pause-per-event only makes sense over
    # reps that actually had >=1 event.
    total_pauses = [p['total_pause'] for p in per_rep]
    avg_pauses = [p['avg_pause'] for p in per_rep if p['avg_pause'] is not None]
    n_zero_event_reps = sum(1 for p in per_rep if p['total'] == 0)
    return dict(
        n_reps=len(per_rep), tps_mean=tps_mean, fail_mean=fail_mean,
        n_zero_event_reps=n_zero_event_reps,
        total_pause_mean=st.mean(total_pauses),
        total_pause_std=st.stdev(total_pauses) if len(total_pauses) > 1 else 0.0,
        avg_pause_mean=st.mean(avg_pauses) if avg_pauses else 0.0,
        avg_pause_std=st.stdev(avg_pauses) if len(avg_pauses) > 1 else 0.0,
        total_gc_mean=st.mean([p['total'] for p in per_rep]),
    )


def main():
    by_key = {}
    for label in CONDITIONS:
        for platform in ('besu', 'nm'):
            d = load_combo(platform, label)
            if d is None:
                print(f'MISSING: {platform}/{label}', file=sys.stderr)
                continue
            by_key[(label, platform)] = d
            print(f"{label:24s} {platform:5s} n={d['n_reps']:3d} "
                  f"(zero-event reps: {d['n_zero_event_reps']}) "
                  f"total_pause={d['total_pause_mean']:7.1f}+-{d['total_pause_std']:6.1f}ms "
                  f"avg_pause={d['avg_pause_mean']:6.2f}+-{d['avg_pause_std']:5.2f}ms",
                  file=sys.stderr)

    label_order = ['anchor_lat', 'tps5', 'tps10', 'tps20', 'slots50', 'slots100',
                   'slots400', 'dur300', 'dur600', 'diag_20tps_400slots']
    def series(dim_labels, dim_vals, metric):
        xb, yb, eb = [], [], []
        xn, yn, en = [], [], []
        for lbl, v in zip(dim_labels, dim_vals):
            b = by_key.get((lbl, 'besu'))
            n = by_key.get((lbl, 'nm'))
            if b:
                xb.append(v); yb.append(b[f'{metric}_mean']); eb.append(b[f'{metric}_std'])
            if n:
                xn.append(v); yn.append(n[f'{metric}_mean']); en.append(n[f'{metric}_std'])
        return (xb, yb, eb), (xn, yn, en)

    def plot_sensitivity(dim_labels, dim_vals, xlabel, fname, title):
        fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
        panel_desc = ['(a) Total GC pause per run', '(b) Average pause per event']
        for ax, metric, ylabel, desc in zip(
                axes, ['total_pause', 'avg_pause'],
                ['Total GC pause / run (ms)', 'Avg pause / event (ms)'],
                panel_desc):
            (xb, yb, eb), (xn, yn, en) = series(dim_labels, dim_vals, metric)
            order_b = np.argsort(xb); order_n = np.argsort(xn)
            xb, yb, eb = np.array(xb)[order_b], np.array(yb)[order_b], np.array(eb)[order_b]
            xn, yn, en = np.array(xn)[order_n], np.array(yn)[order_n], np.array(en)[order_n]
            ax.errorbar(xb, yb, yerr=eb, fmt='o-', color=BLUE, capsize=4, lw=1.8,
                        ms=6, label='Besu (G1GC)')
            ax.errorbar(xn, yn, yerr=en, fmt='s-', color=ORANGE, capsize=4, lw=1.8,
                        ms=6, label='Nethermind (CLR)')
            ax.set_xlabel(f'{xlabel}\n{desc}', fontsize=9.5)
            ax.set_ylabel(ylabel)
            ax.grid(alpha=0.3, zorder=0)
        axes[0].legend(loc='best')
        fig.tight_layout()
        for ext in ('pdf', 'svg', 'png'):
            fig.savefig(f'{OUT}{fname}.{ext}', format=ext, bbox_inches='tight', dpi=200)
        plt.close(fig)
        print(f'  saved {fname}')

    plot_sensitivity(
        ['tps5', 'tps10', 'anchor_lat', 'tps20'], [5, 10, 15, 20],
        'Offered load (tx/s)', 'xray_fig7_ofat_load',
        'Load sensitivity (200 slots/tx, 120s)')

    plot_sensitivity(
        ['slots50', 'slots100', 'anchor_lat', 'slots400'], [50, 100, 200, 400],
        'Slots written per tx', 'xray_fig8_ofat_slots',
        'Workload-intensity sensitivity (15 tx/s, 120s)')

    plot_sensitivity(
        ['anchor_lat', 'dur300', 'dur600'], [120, 300, 600],
        'Stress-window duration (s)', 'xray_fig9_ofat_duration',
        'Duration sensitivity (15 tx/s, 200 slots)')

    print()
    print('=' * 70)
    for label in label_order:
        b = by_key.get((label, 'besu'))
        n = by_key.get((label, 'nm'))
        if b:
            print(f"{label:24s} besu: n={b['n_reps']} "
                  f"pause={b['total_pause_mean']:.1f}±{b['total_pause_std']:.1f}ms "
                  f"avg={b['avg_pause_mean']:.2f}ms")
        if n:
            print(f"{'':24s} nm:   n={n['n_reps']} "
                  f"pause={n['total_pause_mean']:.1f}±{n['total_pause_std']:.1f}ms "
                  f"avg={n['avg_pause_mean']:.2f}ms")


if __name__ == '__main__':
    main()
