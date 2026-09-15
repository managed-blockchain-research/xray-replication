#!/usr/bin/env python3
"""
Run once the n=180 NM PAF-stability rerun is complete. Prints every derived
number needed to update main.tex's Table 3 / surrounding prose / Abstract /
Intro / Contributions / Discussion / Conclusion, all from the SAME pooled,
stress-window-isolated anchor_lat dataset used by Table 4 and Section 8
(critique Option A unification).
"""
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(__file__))
from finalize_anchor_pool import pool_platform, BESU_SOURCES, NM_SOURCES
from xray_stress_window import moments


def rep_level_pauses(platform, sources):
    """Return list of per-rep total pause (ms) and per-rep event count,
    pooled across sources, for bimodal-distribution / sigma reporting."""
    from finalize_anchor_pool import reps_for
    from xray_stress_window import besu_stress_pauses, nm_stress_pauses
    fn = besu_stress_pauses if platform == 'besu' else nm_stress_pauses
    totals, counts = [], []
    for label, run_dir, rep_names in sources:
        if 'n180' in label:
            gr = os.path.join(run_dir, 'good_reps.txt')
            if not os.path.exists(gr) or len(open(gr).read().split()) < 180:
                print(f'WARNING: {label} not complete, skipping', file=sys.stderr)
                continue
        reps = reps_for(run_dir, rep_names)
        for rep in reps:
            events = fn(os.path.join(run_dir, rep), 120)
            pauses = [p for _, p in events]
            totals.append(sum(pauses))
            counts.append(len(pauses))
    return totals, counts


def main():
    besu = pool_platform('besu', BESU_SOURCES, include_partial=False)
    nm = pool_platform('nm', NM_SOURCES, include_partial=False)

    besu_totals, besu_counts = rep_level_pauses('besu', BESU_SOURCES)
    nm_totals, nm_counts = rep_level_pauses('nm', NM_SOURCES)

    print('=' * 70)
    print(f"Besu:  n={len(besu_totals)}  total_pause_mean={st.mean(besu_totals):.1f}ms "
          f"std={st.stdev(besu_totals):.1f}ms  events/rep={st.mean(besu_counts):.2f}")
    print(f"NM:    n={len(nm_totals)}  total_pause_mean={st.mean(nm_totals):.1f}ms "
          f"std={st.stdev(nm_totals):.1f}ms  events/rep={st.mean(nm_counts):.2f}")

    # per-rep-avg convention (matches original paper's "avg_pause_mean" stat)
    besu_avgs = [t / c for t, c in zip(besu_totals, besu_counts) if c > 0]
    nm_avgs = [t / c for t, c in zip(nm_totals, nm_counts) if c > 0]
    print(f"\nBesu avg_pause (per-rep-mean convention) = {st.mean(besu_avgs):.2f}ms "
          f"+-{st.stdev(besu_avgs):.2f}ms  (n_reps_with_events={len(besu_avgs)})")
    print(f"NM   avg_pause (per-rep-mean convention) = {st.mean(nm_avgs):.2f}ms "
          f"+-{st.stdev(nm_avgs):.2f}ms  (n_reps_with_events={len(nm_avgs)})")
    paf_permean = st.mean(nm_avgs) / st.mean(besu_avgs)
    print(f"PAF (per-rep-mean convention, matches original paper stat) = {paf_permean:.2f}x")

    # pooled-per-event convention (matches Section 8's R computation)
    print(f"\nBesu avg_pause (pooled per-event) = {besu['avg_pause_pooled']:.2f}ms")
    print(f"NM   avg_pause (pooled per-event) = {nm['avg_pause_pooled']:.2f}ms")
    paf_pooled = nm['avg_pause_pooled'] / besu['avg_pause_pooled']
    print(f"PAF (pooled-per-event convention) = {paf_pooled:.2f}x")

    # tau_tx
    tau_besu = besu['total_pause_mean'] / besu['tx_count_mean']
    tau_nm = nm['total_pause_mean'] / nm['tx_count_mean']
    print(f"\ntau_tx_besu = {tau_besu:.4f} ms/tx   tau_tx_nm = {tau_nm:.4f} ms/tx   "
          f"ratio = {tau_nm/tau_besu:.2f}x")

    # NM bimodal breakdown (2-cycle vs 3+-cycle runs)
    two_cycle = [t for t, c in zip(nm_totals, nm_counts) if c <= 2]
    three_plus = [t for t, c in zip(nm_totals, nm_counts) if c >= 3]
    print(f"\nNM cycle breakdown: {len(two_cycle)}/{len(nm_totals)} runs with <=2 GC cycles "
          f"(mean total pause {st.mean(two_cycle):.1f}ms), "
          f"{len(three_plus)}/{len(nm_totals)} runs with >=3 cycles "
          f"(range {min(three_plus) if three_plus else 0:.0f}-{max(three_plus) if three_plus else 0:.0f}ms)")
    zero_event = sum(1 for c in nm_counts if c == 0)
    print(f"NM zero-event reps: {zero_event}/{len(nm_counts)}")

    print(f"\nResidual R (Section 8, pooled moments): besu={besu['residual_R']:.2f}ms nm={nm['residual_R']:.2f}ms "
          f"ratio={nm['residual_R']/besu['residual_R']:.2f}x")

    out = dict(besu=besu, nm=nm,
               paf_permean=paf_permean, paf_pooled=paf_pooled,
               tau_besu=tau_besu, tau_nm=tau_nm, tau_ratio=tau_nm/tau_besu,
               besu_n_reps=len(besu_totals), nm_n_reps=len(nm_totals),
               besu_events_per_rep=st.mean(besu_counts), nm_events_per_rep=st.mean(nm_counts),
               nm_two_cycle_n=len(two_cycle), nm_three_plus_n=len(three_plus),
               nm_two_cycle_mean=st.mean(two_cycle) if two_cycle else None,
               nm_three_plus_range=(min(three_plus), max(three_plus)) if three_plus else None,
               nm_zero_event_reps=zero_event)
    with open('/tmp/xray_narrative_numbers.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('\nsaved /tmp/xray_narrative_numbers.json')


if __name__ == '__main__':
    main()
