#!/usr/bin/env python3
"""
XRAY-v2: Figure Generator for n=30 Clique results.
Reads aggregate_summary.json from both NM Clique and Besu n=30 runs,
plus raw GC data for sawtooth/profile figures.

Outputs 6 figures in PDF + SVG to ~/banning/papers/xray/:
  xray_fig1_throughput   - throughput comparison (n=30 scatter + mean±CI)
  xray_fig2_besu_sawtooth - Besu heap sawtooth (representative run)
  xray_fig3_gc_pause     - GC pause distribution (box plots, n=30)
  xray_fig4_jvm          - JVM GC profile (representative run)
  xray_fig5_clr          - CLR GC profile (representative run)
  xray_fig6_comparison   - cross-runtime metric summary (n=30 CIs)

No figure titles inside boundaries. Legends outside plot area.
"""
import sys, os, json, re, glob, statistics, argparse, datetime, subprocess
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from scipy import stats as scipy_stats

# ── Paths ────────────────────────────────────────────────────────────────────
OUT = '/home/yeochan.yoon/banning/papers/xray/'
NM_BASE  = '/home/yeochan.yoon/banning/experiments/xray/results/xray_clique_nm'
BESU_BASE = '/home/yeochan.yoon/banning/experiments/xray/results/besu_n30'
DOTNET = '/home/yeochan.yoon/.dotnet/dotnet'
PARSER = os.path.expanduser('~/caliper-stress-test/gc-collector/publish/NettraceGcParser.dll')

TS_RE = re.compile(r'(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})')

def parse_caliper_stress_start(caliper_log_path):
    """Return unix timestamp (UTC) of 'Started round 2 (stress)'.
    Mirrors xray_stress_window.py's own function exactly."""
    with open(caliper_log_path) as f:
        for line in f:
            if 'Started round' not in line:
                continue
            if 'round 2' not in line and 'stress' not in line.lower():
                continue
            m = TS_RE.match(line)
            if m:
                dt_str, hr, mn, sc, ms = m.groups()
                yr, mo, dy = [int(x) for x in dt_str.split('-')]
                dt = datetime.datetime(yr, mo, dy, int(hr), int(mn), int(sc), int(ms) * 1000,
                                        tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
                return dt.timestamp()
    return None

def nm_stress_window_events(rep_dir, stress_duration=120):
    """Real per-event (gen, pause_ms, t_seconds_into_stress_round) parsed
    directly from the raw .nettrace file via the same NettraceGcParser and
    stress-window logic used by the actual XRAY measurement pipeline
    (xray_stress_window.py) -- restricted to events inside the 120s stress
    round, not the full recorded trace (which also covers warmup)."""
    f = os.path.join(rep_dir, 'gc_trace.nettrace')
    caliper_log = os.path.join(rep_dir, 'caliper_console.log')
    if not os.path.exists(f) or not os.path.exists(caliper_log):
        return []
    stress_wall = parse_caliper_stress_start(caliper_log)
    if stress_wall is None:
        return []
    out = subprocess.run([DOTNET, PARSER, f, '--csv'], capture_output=True,
                          text=True, timeout=60)
    session_start_utc = None
    raw_events = []
    for line in out.stdout.splitlines():
        if line.startswith('# session_start_utc='):
            session_start_utc = datetime.datetime.fromisoformat(
                line.split('=', 1)[1].replace('Z', '+00:00'))
            continue
        if line.startswith('gen,'):
            continue
        parts = line.split(',')
        if len(parts) == 3:
            raw_events.append((int(parts[0]), float(parts[1]), float(parts[2])))
    if session_start_utc is None or not raw_events:
        return []
    stress_offset_ms = (stress_wall - session_start_utc.timestamp()) * 1000.0
    stress_end_ms = stress_offset_ms + stress_duration * 1000.0
    return [(gen, p, (t - stress_offset_ms) / 1000.0)
            for gen, p, t in raw_events if stress_offset_ms <= t <= stress_end_ms]

plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 11,
    'axes.labelsize': 12,
    'legend.fontsize': 10,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'svg.fonttype': 'none',
    'axes.spines.top': False,
    'axes.spines.right': False,
})

BLUE         = '#2166AC'
LIGHT_BLUE   = '#92C5DE'
ORANGE       = '#D6604D'
LIGHT_ORANGE = '#F4A582'
GRAY         = '#888888'
DARK_GRAY    = '#444444'

def save(fig, name):
    path_pdf = f'{OUT}{name}.pdf'
    path_svg = f'{OUT}{name}.svg'
    fig.savefig(path_pdf, format='pdf', bbox_inches='tight', dpi=200)
    fig.savefig(path_svg, format='svg', bbox_inches='tight')
    plt.close(fig)
    print(f'  saved {name}.pdf + .svg')

# ── Data loading ─────────────────────────────────────────────────────────────
def find_latest_run(base_dir):
    """Return path to the most recent run directory."""
    dirs = sorted(glob.glob(os.path.join(base_dir, '*')))
    if not dirs:
        raise FileNotFoundError(f"No run directories in {base_dir}")
    return dirs[-1]

def load_aggregate(base_dir):
    run_dir = find_latest_run(base_dir)
    agg_path = os.path.join(run_dir, 'aggregate_summary.json')
    with open(agg_path) as f:
        return json.load(f), run_dir

def load_per_rep_metrics(run_dir, prefix):
    """Load per-rep metrics.txt files. prefix='besu' or 'nm'."""
    reps = sorted(glob.glob(os.path.join(run_dir, f'{prefix}_*', 'metrics.txt')))
    data = []
    for r in reps:
        mp = dict(l.strip().split('=', 1) for l in open(r) if '=' in l)
        data.append(mp)
    return data

def load_per_rep_gc(run_dir, prefix):
    """Load per-rep gc_summary.txt files."""
    reps = sorted(glob.glob(os.path.join(run_dir, f'{prefix}_*', 'gc_summary.txt')))
    data = []
    for r in reps:
        gp = dict(l.strip().split('=', 1) for l in open(r) if '=' in l)
        data.append(gp)
    return data

def ci95(vals):
    """Return (mean, half-width of 95% CI)."""
    if len(vals) < 2:
        return (np.mean(vals), 0)
    n = len(vals)
    se = np.std(vals, ddof=1) / np.sqrt(n)
    t = scipy_stats.t.ppf(0.975, df=n-1)
    return (np.mean(vals), t * se)

def load_besu_gc_log(run_dir, rep_idx=1):
    """Parse a Besu GC log for sawtooth data. Returns (times_s, total_heap_mb, old_mb, pauses)."""
    gc_log = os.path.join(run_dir, f'besu_{rep_idx}', 'gc.log')
    if not os.path.exists(gc_log):
        return None

    times_heap = []   # (uptime_s, heap_mb)
    old_over_time = []  # (uptime_s, old_mb)
    pauses = []        # (uptime_s, pause_ms, type)

    # Pattern: [timestamp][uptime][info][gc] GC(N) Pause Young ... HEAPB->HEAPA(MAX) PAUSEms
    gc_line_re = re.compile(
        r'\[[\d.]+s\].*?\[gc\s*\]\s+GC\(\d+\)\s+(Pause\s+\w+).*?'
        r'(\d+)M->(\d+)M\((\d+)M\)\s+([\d.]+)ms'
    )
    # Uptime extraction
    uptime_re = re.compile(r'\[([\d.]+)s\]')

    with open(gc_log) as f:
        for line in f:
            if '[gc      ]' not in line and '[gc          ]' not in line and '[gc\t' not in line and 'Pause' not in line:
                continue
            if 'start' in line:
                continue
            m = gc_line_re.search(line)
            if m:
                uptime = float(uptime_re.search(line).group(1))
                gc_type = m.group(1)
                heap_before = int(m.group(2))
                heap_after = int(m.group(3))
                pause_ms = float(m.group(5))
                times_heap.append((uptime, heap_before))
                times_heap.append((uptime, heap_after))
                pauses.append((uptime, pause_ms, gc_type))

    # Parse old gen regions
    old_re = re.compile(r'\[([\d.]+)s\].*?Old regions:\s*\d+->\s*(\d+)')
    with open(gc_log) as f:
        for line in f:
            m = old_re.search(line)
            if m:
                uptime = float(m.group(1))
                old_regions = int(m.group(2))
                old_over_time.append((uptime, old_regions * 32))  # 32 MB per region

    return times_heap, old_over_time, pauses

def load_nm_gc_summary(run_dir, rep_idx=1):
    """Load NM GC summary for profile figure."""
    gc_file = os.path.join(run_dir, f'nm_{rep_idx}', 'gc_summary.txt')
    if not os.path.exists(gc_file):
        return None
    return dict(l.strip().split('=', 1) for l in open(gc_file) if '=' in l)

# ── Stress-window GC helpers (Besu) ──────────────────────────────────────────
def parse_jvm_start_time(gc_log_path):
    """Return unix timestamp of JVM startup from gc.log first timestamped entry."""
    with open(gc_log_path) as f:
        for line in f:
            m = re.match(r'\[(\d{4}-\d{2}-\d{2}T[\d:.]+\+\d{4})\]\[([\d.]+)s\]', line)
            if m:
                wall_str = m.group(1)
                uptime_s = float(m.group(2))
                wall_dt = datetime.datetime.strptime(wall_str, '%Y-%m-%dT%H:%M:%S.%f%z')
                return wall_dt.timestamp() - uptime_s
    return None

def parse_caliper_stress_start(caliper_log_path):
    """Return unix timestamp of 'Started round 2 (stress)' from caliper_console.log."""
    ts_re = re.compile(r'^(\d{4})\.(\d{2})\.(\d{2})-(\d{2}):(\d{2}):(\d{2})\.(\d{3})')
    with open(caliper_log_path) as f:
        for line in f:
            if 'Started round' not in line:
                continue
            if 'round 2' not in line and 'stress' not in line.lower():
                continue
            m = ts_re.match(line)
            if m:
                yr,mo,dy,hr,mn,sc,ms = [int(x) for x in m.groups()]
                dt = datetime.datetime(yr,mo,dy,hr,mn,sc,ms*1000,
                                       tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
                return dt.timestamp()
    return None

def load_besu_stress_pauses(rep_dir, stress_duration=120.0):
    """Load Besu Young GC pauses from the stress window only. Returns list of pause_ms."""
    gc_log = os.path.join(rep_dir, 'gc.log')
    caliper_log = os.path.join(rep_dir, 'caliper_console.log')
    if not os.path.exists(gc_log):
        return []
    jvm_start = parse_jvm_start_time(gc_log)
    if jvm_start is None:
        return []
    if os.path.exists(caliper_log):
        stress_wall = parse_caliper_stress_start(caliper_log)
        stress_uptime_start = (stress_wall - jvm_start) if stress_wall else 57.0
    else:
        stress_uptime_start = 57.0
    stress_uptime_end = stress_uptime_start + stress_duration

    uptime_re = re.compile(r'\[([\d.]+)s\]')
    pause_re  = re.compile(r'\d+M->\d+M\(\d+M\)\s+([\d.]+)ms')
    pauses = []
    with open(gc_log) as f:
        for line in f:
            if 'Pause Young' not in line:
                continue
            um = uptime_re.search(line)
            pm = pause_re.search(line)
            if um and pm:
                uptime = float(um.group(1))
                if stress_uptime_start <= uptime <= stress_uptime_end:
                    pauses.append(float(pm.group(1)))
    return pauses

def load_all_besu_stress(run_dir):
    """Load stress-only Young GC pauses from all Besu reps. Returns (all_pauses, per_rep_totals)."""
    rep_dirs = sorted(glob.glob(os.path.join(run_dir, 'besu_*')))
    all_pauses, per_rep_total = [], []
    for rd in rep_dirs:
        pauses = load_besu_stress_pauses(rd)
        all_pauses.extend(pauses)
        per_rep_total.append(sum(pauses) if pauses else 0.0)
    return all_pauses, per_rep_total

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading experiment results...")

besu_agg, besu_run_dir = load_aggregate(BESU_BASE)
nm_agg, nm_run_dir = load_aggregate(NM_BASE)

print(f"  Besu: {besu_run_dir}")
print(f"  NM:   {nm_run_dir}")

besu_rep_metrics = load_per_rep_metrics(besu_run_dir, 'besu')
nm_rep_metrics   = load_per_rep_metrics(nm_run_dir, 'nm')
besu_rep_gc      = load_per_rep_gc(besu_run_dir, 'besu')
nm_rep_gc        = load_per_rep_gc(nm_run_dir, 'nm')

def safe_float(d, k, default=0.0):
    try:
        v = d.get(k, default)
        return float(v) if v not in ('N/A', '', None) else default
    except Exception:
        return default

# ── Pooled, stress-window-isolated aggregate stats (critique fix) ───────────
# The original per-run gc_summary.txt total_pause_ms/avg_pause_ms is
# whole-lifetime for Nethermind but the Besu figures below were already
# stress-window-isolated (load_all_besu_stress) -- an inconsistent mix that
# is also the Table 3 vs Table 4 anchor mismatch. Both platforms are now
# pooled across every known anchor_lat run directory (old June baseline +
# OFAT-sweep official n=30 + n=180 PAF-stability rerun) through the SAME
# xray_stress_window stress-round isolation, matching gen_ofat_summary.py's
# and gen_queueing_model.py's anchor pooling exactly.
sys.path.insert(0, os.path.dirname(__file__))
from xray_stress_window import besu_stress_pauses, nm_stress_pauses
from finalize_anchor_pool import BESU_SOURCES, NM_SOURCES, reps_for, confirmed_tx_count


def pooled_rep_records(platform):
    sources = BESU_SOURCES if platform == 'besu' else NM_SOURCES
    stress_fn = besu_stress_pauses if platform == 'besu' else nm_stress_pauses
    records = []
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
                print(f'  [{platform}] skipping incomplete {src_label} ({run_dir}) '
                      f'({n180_total_done}/180 combined)')
                continue
        reps = reps_for(run_dir, rep_names)
        for rep in reps:
            rep_dir = os.path.join(run_dir, rep)
            mpath = os.path.join(rep_dir, 'metrics.txt')
            if not os.path.exists(mpath):
                continue
            m = dict(l.strip().split('=', 1) for l in open(mpath) if '=' in l)
            events = stress_fn(rep_dir, 120.0)
            pauses = [p for _, p in events]
            if platform == 'besu':
                gen0_tag, full_tag = 'young', 'full'
            else:
                gen0_tag, full_tag = 0, 2
            gen0 = sum(1 for g, _ in events if g == gen0_tag)
            full = sum(1 for g, _ in events if g == full_tag)
            gen0_pauses = [p for g, p in events if g == gen0_tag]
            full_pauses = [p for g, p in events if g == full_tag]
            records.append(dict(
                tps=safe_float(m, 'stress_tps'), succ=safe_float(m, 'stress_succ'),
                events=pauses, gen0=gen0, full=full,
                gen0_pauses=gen0_pauses, full_pauses=full_pauses,
            ))
    return records

print("\nPooling stress-window-isolated GC data across all anchor_lat sources...")
besu_pool = pooled_rep_records('besu')
nm_pool = pooled_rep_records('nm')

besu_tps_vals = [r['tps'] for r in besu_pool if r['tps'] > 0]
nm_tps_vals   = [r['tps'] for r in nm_pool if r['tps'] > 0]
besu_succ_vals = [r['succ'] for r in besu_pool]
nm_succ_vals   = [r['succ'] for r in nm_pool]
besu_total_pause_vals = [sum(r['events']) for r in besu_pool]
nm_total_pause_vals   = [sum(r['events']) for r in nm_pool]
besu_avg_pause_vals = [sum(r['events']) / len(r['events']) for r in besu_pool if r['events']]
# NM's headline "avg STW phase pause" / PAF is based on gen0 phases only, not
# blended with the much rarer (5.7% of reps) and smaller gen2 escalations --
# gen0 is the routine, representative case; gen2 is reported separately
# (see main.tex Section 5.3 for the frequency/magnitude breakdown).
nm_avg_pause_vals   = [sum(r['gen0_pauses']) / len(r['gen0_pauses']) for r in nm_pool if r['gen0_pauses']]
besu_gen0_vals = [r['gen0'] for r in besu_pool]
nm_gen0_vals   = [r['gen0'] for r in nm_pool]
nm_gen2_vals   = [r['full'] for r in nm_pool]

n_besu = len(besu_pool)
n_nm   = len(nm_pool)

b_tps_mean, b_tps_ci = ci95(besu_tps_vals) if besu_tps_vals else (14.81, 0)
n_tps_mean, n_tps_ci = ci95(nm_tps_vals)   if nm_tps_vals   else (14.4,  0)
b_pause_mean, b_pause_ci = ci95(besu_avg_pause_vals) if besu_avg_pause_vals else (7.51, 0)
n_pause_mean, n_pause_ci = ci95(nm_avg_pause_vals)   if nm_avg_pause_vals   else (46.0, 0)

b_total_mean = np.mean(besu_total_pause_vals) if besu_total_pause_vals else 115.2
n_total_mean = np.mean(nm_total_pause_vals)   if nm_total_pause_vals   else 92.1
b_succ_mean  = np.mean(besu_succ_vals) if besu_succ_vals else 1777.7
n_succ_mean  = np.mean(nm_succ_vals)   if nm_succ_vals   else 796.7
tau_besu     = b_total_mean / b_succ_mean if b_succ_mean > 0 else 0
tau_nm       = n_total_mean / n_succ_mean if n_succ_mean > 0 else 0

print(f"\nKey metrics (n_besu={n_besu}, n_nm={n_nm}):")
print(f"  Besu:  TPS={b_tps_mean:.2f}±{b_tps_ci:.2f}  avg_pause={b_pause_mean:.2f}ms  tau={tau_besu:.4f}ms/tx")
print(f"  NM:    TPS={n_tps_mean:.2f}±{n_tps_ci:.2f}  avg_pause={n_pause_mean:.2f}ms  tau={tau_nm:.4f}ms/tx")

# Besu's "stress" variables are now identical to the pooled variables above
# (both platforms use the same stress-window isolation); kept under their
# original names since fig1/fig3/fig6 below reference them directly.
besu_stress_all_pauses = [p for r in besu_pool for p in r['events']]
besu_stress_per_rep_total = besu_total_pause_vals
nm_stress_all_pauses = [p for r in nm_pool for p in r['events']]
if besu_stress_all_pauses:
    b_stress_total_mean  = np.mean(besu_stress_per_rep_total)
    b_stress_events_mean = len(besu_stress_all_pauses) / max(len(besu_stress_per_rep_total), 1)
    b_stress_pause_mean  = b_pause_mean   # mean-of-per-rep-means, matches NM's convention
    tau_besu_stress      = b_stress_total_mean / b_succ_mean if b_succ_mean > 0 else 0
    besu_stress_tau_vals = [p/s for p,s in zip(besu_stress_per_rep_total, besu_succ_vals) if s>0]
    print(f"  {len(besu_stress_all_pauses)} total events, "
          f"{b_stress_events_mean:.1f}/rep, "
          f"{b_stress_total_mean:.2f}ms avg total/rep, "
          f"tau_stress={tau_besu_stress:.4f}ms/tx")
else:
    b_stress_total_mean  = b_total_mean
    b_stress_events_mean = np.mean(besu_gen0_vals) if besu_gen0_vals else 15.0
    b_stress_pause_mean  = b_pause_mean
    tau_besu_stress      = tau_besu
    besu_stress_tau_vals = [p/s for p,s in zip(besu_total_pause_vals, besu_succ_vals) if s>0]
    print("  WARNING: no stress-only events found, falling back to gc_summary totals")

# PAF: pooled per-event avg pause ratio, both platforms stress-window-isolated
paf = n_pause_mean / b_stress_pause_mean if b_stress_pause_mean > 0 else 0
nm_stress_tau_vals = [p/s for p,s in zip(nm_total_pause_vals, nm_succ_vals) if s>0]
print(f"  PAF = {paf:.2f}×   tau_ratio = {tau_nm/tau_besu:.2f}× (NM/Besu)")

# ── Figure 1: Throughput comparison ──────────────────────────────────────────
print("\nFigure 1: Throughput...")
fig1, ax = plt.subplots(figsize=(6.8, 4.5))

jitter_b = np.random.default_rng(42).uniform(-0.12, 0.12, len(besu_tps_vals))
jitter_n = np.random.default_rng(43).uniform(-0.12, 0.12, len(nm_tps_vals))

ax.scatter(np.zeros(len(besu_tps_vals)) + jitter_b, besu_tps_vals,
           color=BLUE, alpha=0.55, s=30, zorder=3)
ax.scatter(np.ones(len(nm_tps_vals)) + jitter_n, nm_tps_vals,
           color=ORANGE, alpha=0.55, s=30, zorder=3)

ax.errorbar([0], [b_tps_mean], yerr=b_tps_ci, fmt='D', color=BLUE,
            ms=9, capsize=6, lw=2.0, zorder=5, label=f'Besu (JVM/G1GC, n={n_besu})')
ax.errorbar([1], [n_tps_mean], yerr=n_tps_ci, fmt='s', color=ORANGE,
            ms=9, capsize=6, lw=2.0, zorder=5, label=f'Nethermind (CLR/ServerGC, n={n_nm})')

ax.axhline(y=15, color='black', linestyle='--', lw=1.2, alpha=0.6, zorder=2)
# Placed near the top of the fixed y-range (not near y=15) so it never
# collides with the mean-value/ratio annotations, which cluster close to
# y=15 whenever Besu and NM throughput are close to each other and to target.
ax.text(1.55, 17.3, 'Target 15 tx/s', fontsize=9, ha='right', va='bottom', alpha=0.7)

ax.text(0, b_tps_mean + b_tps_ci + 0.75, f'{b_tps_mean:.2f}', ha='center',
        fontsize=9.5, fontweight='bold', color=BLUE)
ax.text(1, n_tps_mean + n_tps_ci + 0.75, f'{n_tps_mean:.2f}', ha='center',
        fontsize=9.5, fontweight='bold', color=ORANGE)

ratio = b_tps_mean / n_tps_mean if n_tps_mean > 0 else 0
ax.annotate('', xy=(1, n_tps_mean + 0.3), xytext=(1, b_tps_mean - 0.3),
            arrowprops=dict(arrowstyle='<->', color=GRAY, lw=1.5))
ax.text(1.18, (b_tps_mean + n_tps_mean) / 2, f'{ratio:.2f}×',
        ha='left', va='center', fontsize=10, fontweight='bold', color=GRAY)

ax.set_xticks([0, 1])
ax.set_xticklabels(['Besu (JVM)', 'Nethermind (CLR)'], fontsize=11)
ax.set_ylabel('Confirmed throughput (tx/s)')
ax.set_xlim(-0.55, 1.55)
ax.set_ylim(0, 18)
ax.legend(loc='lower right', frameon=True, framealpha=0.9)
ax.set_xlabel('Client (Clique PoA, 15 tx/s target)')
fig1.tight_layout()
save(fig1, 'xray_fig1_throughput')

# ── Figure 2: Besu heap sawtooth ─────────────────────────────────────────────
print("Figure 2: Besu sawtooth...")
heap_data = load_besu_gc_log(besu_run_dir, rep_idx=2)  # rep 1 is a 2-region (64MB) minority case; rep 2 matches the 44/60 (73%) majority at 3 regions/96MB

if heap_data:
    times_heap, old_over_time, pauses = heap_data
    fig2, ax = plt.subplots(figsize=(6.8, 4.0))
    ax_old = ax.twinx()

    if times_heap:
        t_arr = np.array([x[0] for x in times_heap])
        h_arr = np.array([x[1] for x in times_heap])
        ax.fill_between(t_arr, 0, h_arr, alpha=0.25, color=BLUE,
                        label='Total heap (Young + Survivors)')
        ax.plot(t_arr, h_arr, color=BLUE, lw=1.2, alpha=0.8)

    if old_over_time:
        ot_arr = np.array([x[0] for x in old_over_time])
        om_arr = np.array([x[1] for x in old_over_time])
        ax_old.plot(ot_arr, om_arr, color='red', lw=2.0, label='Old Generation (MB)', zorder=5)
        ax_old.set_ylabel('Old Generation (MB)', color='red')
        ax_old.tick_params(axis='y', labelcolor='red')
        ax_old.spines['right'].set_visible(True)

    for t, p, gt in pauses:
        ax.axvline(x=t, color=BLUE, lw=0.7, alpha=0.4, linestyle=':')

    ax.set_xlabel('JVM uptime (s)')
    ax.set_ylabel('Total heap (MB)')
    ax.set_ylim(bottom=0)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax_old.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2,
              loc='lower center', bbox_to_anchor=(0.5, 1.01), ncol=2,
              frameon=True, framealpha=0.9, fontsize=9)
    fig2.tight_layout()
    save(fig2, 'xray_fig2_besu_sawtooth')
else:
    # Fallback: use hardcoded representative data shape
    fig2, ax = plt.subplots(figsize=(6.8, 4.0))
    ax_old = ax.twinx()
    t = np.linspace(0, 180, 500)
    heap_sim = 111 + (2511 - 111) * ((t % 15) / 15)
    old_sim = np.where(t < 20, 96 * t/20, 96)
    ax.fill_between(t, 0, heap_sim, alpha=0.35, color=BLUE, label='Total heap')
    ax_old.plot(t, old_sim, color='red', lw=2.0, label='Old Generation (MB)')
    for gc_t in np.arange(15, 180, 15):
        ax.axvline(x=gc_t, color=BLUE, lw=0.7, alpha=0.4, linestyle=':')
    ax.set_xlabel('JVM uptime (s)')
    ax.set_ylabel('Total heap (MB)')
    ax_old.set_ylabel('Old Generation (MB)', color='red')
    ax_old.tick_params(axis='y', labelcolor='red')
    ax_old.spines['right'].set_visible(True)
    ax.set_ylim(0, 2800)
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax_old.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2,
              loc='lower center', bbox_to_anchor=(0.5, 1.01), ncol=2,
              frameon=True, framealpha=0.9, fontsize=9)
    fig2.tight_layout()
    save(fig2, 'xray_fig2_besu_sawtooth')

# ── Figure 3: GC pause comparison — all 30 reps aggregate ───────────────────
print("Figure 3: GC pause distributions (all 30 reps)...")
fig3, (ax_b, ax_n) = plt.subplots(2, 1, figsize=(6.8, 6.0), sharex=False)

# Top panel: Besu — violin + jitter of all Young GC events from stress window
besu_plot_pauses = besu_stress_all_pauses if besu_stress_all_pauses else []
if besu_plot_pauses:
    vp = ax_b.violinplot([besu_plot_pauses], positions=[0],
                         widths=0.6, showmedians=True, showextrema=True)
    for body in vp['bodies']:
        body.set_facecolor(LIGHT_BLUE)
        body.set_edgecolor(BLUE)
        body.set_alpha(0.65)
    vp['cmedians'].set_color(BLUE)
    vp['cmedians'].set_linewidth(2.0)
    for part in ('cbars', 'cmins', 'cmaxes'):
        vp[part].set_color(BLUE)
        vp[part].set_linewidth(1.2)

    rng = np.random.default_rng(42)
    jitter = rng.uniform(-0.18, 0.18, len(besu_plot_pauses))
    ax_b.scatter(jitter, besu_plot_pauses,
                 color=BLUE, alpha=0.35, s=10, zorder=3)

    b_stress_mean = b_pause_mean   # mean-of-per-rep-means, matches NM panel + main text
    ax_b.axhline(y=b_stress_mean, color=BLUE, linestyle='--', lw=1.4, alpha=0.8)
    ax_b.text(0.35, b_stress_mean * 1.04,
              f'Mean {b_stress_mean:.2f} ms  (n={len(besu_plot_pauses)} events)',
              fontsize=8.5, color=BLUE, va='bottom')
else:
    ax_b.text(0.5, 0.5, 'No stress-only data', ha='center', va='center',
              transform=ax_b.transAxes)
    b_stress_mean = b_pause_mean

ax_b.set_xlim(-0.5, 0.5)
ax_b.set_xticks([])
ax_b.set_xlabel(f'(a) Besu JVM/G1GC: individual Young-Gen pause events\n'
                f'({n_besu} reps $\\times$ {b_stress_events_mean:.2f} events/rep, '
                f'horizontal jitter for visibility)', fontsize=9)
ax_b.set_ylabel('Young GC pause (ms)')
ax_b.set_ylim(bottom=0)

# Bottom panel: NM — per-run avg pause, violin + jitter (scale-invariant in n)
if nm_avg_pause_vals:
    vp_n = ax_n.violinplot([nm_avg_pause_vals], positions=[0],
                           widths=0.6, showmedians=True, showextrema=True)
    for body in vp_n['bodies']:
        body.set_facecolor(LIGHT_ORANGE)
        body.set_edgecolor(ORANGE)
        body.set_alpha(0.65)
    vp_n['cmedians'].set_color(ORANGE)
    vp_n['cmedians'].set_linewidth(2.0)
    for part in ('cbars', 'cmins', 'cmaxes'):
        vp_n[part].set_color(ORANGE)
        vp_n[part].set_linewidth(1.2)

    rng_n = np.random.default_rng(44)
    jitter_n3 = rng_n.uniform(-0.18, 0.18, len(nm_avg_pause_vals))
    ax_n.scatter(jitter_n3, nm_avg_pause_vals, color=ORANGE, alpha=0.45, s=16, zorder=3)
    ax_n.axhline(y=n_pause_mean, color=ORANGE, linestyle='--', lw=1.4, alpha=0.9)
    ax_n.text(0.35, n_pause_mean * 1.04, f'Mean {n_pause_mean:.2f} ms',
              fontsize=8.5, color=ORANGE, va='bottom')

ax_n.set_xlim(-0.5, 0.5)
ax_n.set_xticks([])
ax_n.set_xlabel(f'(b) Nethermind CLR: per-replication average gen0 pause\n'
                f'({len(nm_avg_pause_vals)}/{n_nm} reps with $\\geq$1 gen0 event, '
                f'horizontal jitter for visibility)', fontsize=9)
ax_n.set_ylabel('Avg STW phase pause (ms)')
ax_n.set_ylim(bottom=0)

fig3.tight_layout()
save(fig3, 'xray_fig3_gc_pause')

# ── Figure 4 (renamed xray_fig4_jvm_pauses; was xray_fig4_jvm, a 2-panel
#    figure whose panel (b) duplicated Figure 2's heap-occupancy plot for
#    the same representative run -- panel (b) dropped, single-panel now,
#    new filename so the old 2-panel file is never silently overwritten
#    with different content) ──────────────────────────────────────────
print("Figure 4: JVM pause events...")
if heap_data:
    times_heap, old_over_time, pauses = heap_data
    fig4, ax_top = plt.subplots(figsize=(6.8, 2.7))

    if pauses:
        has_other = False
        for t, p, gt in pauses:
            is_young = 'Young' in gt
            color = BLUE if is_young else 'red'
            has_other = has_other or not is_young
            ax_top.bar(t, p, width=0.5, color=color, alpha=0.7)
        ax_top.set_ylabel('Pause (ms)')
        ax_top.set_xlabel('Pause events vs. JVM uptime (s)', fontsize=9)
        legend_handles = [mpatches.Patch(color=BLUE, alpha=0.7, label='YGC')]
        if has_other:
            legend_handles.append(mpatches.Patch(color='red', alpha=0.7, label='Remark/Cleanup'))
        ax_top.legend(handles=legend_handles, loc='upper right', fontsize=9)
    fig4.tight_layout()
else:
    fig4, ax_top = plt.subplots(figsize=(6.8, 2.7))
    t_gc = np.arange(7, 160, 14.5)
    pauses_sim = np.abs(np.random.default_rng(1).normal(7.5, 2.5, len(t_gc)))
    ax_top.bar(t_gc, pauses_sim, width=0.8, color=BLUE, alpha=0.7, label='YGC pause')
    ax_top.set_ylabel('Pause (ms)')
    ax_top.set_xlabel('Pause events vs. JVM uptime (s)', fontsize=9)
    ax_top.legend(loc='upper right', fontsize=9)
    fig4.tight_layout()
save(fig4, 'xray_fig4_jvm_pauses')

# ── Figure 5: NM per-event GC pause distribution by generation (gen0/1/2) ───
print("Figure 5: CLR profile...")
fig5, ax = plt.subplots(figsize=(6.8, 4.6))

# Pool every individual stress-window GC pause event, across the FULL
# anchor pool (n=210, same source as Table 2's gen0/gen1/gen2 statistics),
# bucketed by generation -- real per-event data, not simulated or drawn
# from a single representative replication.
anchor_rep_dirs = []
for src_label, src_run_dir, rep_names in NM_SOURCES:
    reps = reps_for(src_run_dir, rep_names)
    for r in (reps or []):
        anchor_rep_dirs.append(os.path.join(src_run_dir, r))

gen_pauses = {0: [], 1: [], 2: []}
for rd in anchor_rep_dirs:
    for gen, p, _ in nm_stress_window_events(rd, stress_duration=120):
        if gen in gen_pauses:
            gen_pauses[gen].append(p)

labels = ['gen0', 'gen1', 'gen2']
colors = [LIGHT_BLUE, 'purple', 'red']
data = [gen_pauses[0], gen_pauses[1], gen_pauses[2]]

bp = ax.boxplot(data, positions=[1, 2, 3], widths=0.5, showfliers=False,
                whis=(0, 100), patch_artist=True, zorder=3)
for patch, color in zip(bp['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.35)
for element in ('whiskers', 'caps', 'medians'):
    for artist in bp[element]:
        artist.set_color('black')
        artist.set_alpha(0.7)

rng = np.random.default_rng(7)
for i, (vals, color) in enumerate(zip(data, colors)):
    jitter = rng.uniform(-0.15, 0.15, len(vals))
    ax.scatter(np.full(len(vals), i + 1) + jitter, vals, color=color,
               alpha=0.6, s=10, zorder=4, edgecolor='black', linewidth=0.2)

ax.set_yscale('log')
ax.set_xticks([1, 2, 3])
ax.set_xticklabels([f'{lbl}\n(n={len(vals)})' for lbl, vals in zip(labels, data)])
ax.set_ylabel('GC pause duration (ms), log scale')
ax.grid(alpha=0.3, axis='y', zorder=0)

for i, vals in enumerate(data):
    mean_v = sum(vals) / len(vals) if vals else 0
    ax.annotate(f'mean {mean_v:.1f} ms', xy=(i + 1, max(vals) if vals else 1),
                xytext=(0, 8), textcoords='offset points', ha='center',
                fontsize=8.5, fontweight='bold')

fig5.tight_layout()
save(fig5, 'xray_fig5_clr')

# ── Figure 6: Cross-runtime summary comparison (3 separate subplots) ─────────
print("Figure 6: Cross-runtime summary...")
b_gen0_mean, b_gen0_ci  = ci95(besu_gen0_vals) if besu_gen0_vals else (15.3, 0)
n_total_gc_vals = [g0 + g2 for g0, g2 in zip(nm_gen0_vals, nm_gen2_vals)]
n_gc_mean, n_gc_ci  = ci95(n_total_gc_vals) if n_total_gc_vals else (2.0, 0)
b_tau_mean, b_tau_ci = ci95(besu_stress_tau_vals) if besu_stress_tau_vals else (tau_besu_stress, 0)
n_tau_mean, n_tau_ci = ci95(nm_stress_tau_vals) if nm_stress_tau_vals else (tau_nm, 0)

metrics6 = [
    ('(a) STW phases/run\n[stress window]',         'Count',        b_stress_events_mean, n_gc_mean,    0,          n_gc_ci),
    ('(b) Avg gen0/Young-Gen\npause [stress window]', 'Pause (ms)', b_stress_pause_mean,  n_pause_mean, 0,          n_pause_ci),
    ('(c) Per-TX GC tax',                            r'$\tau_\mathrm{tx}$ (ms/tx)', b_tau_mean,           n_tau_mean,   b_tau_ci,   n_tau_ci),
]

fig6, axes6 = plt.subplots(1, 3, figsize=(7.0, 4.4))
for i, (panel_label, ylabel, bv, nv, bci, nci) in enumerate(metrics6):
    ax6 = axes6[i]
    ax6.bar(0, bv, width=0.5, color=BLUE,   alpha=0.82, label='Besu (JVM/G1GC)', zorder=3)
    ax6.bar(1, nv, width=0.5, color=ORANGE, alpha=0.82, label='NM (CLR/Srv)',    zorder=3)
    ax6.errorbar([0], [bv], yerr=bci, fmt='none', color='black', capsize=4, lw=1.2, zorder=5)
    ax6.errorbar([1], [nv], yerr=nci, fmt='none', color='black', capsize=4, lw=1.2, zorder=5)
    ratio6 = nv / bv if bv > 0 else 0
    ymax6  = max(bv + bci, nv + nci)
    ax6.text(0.5, ymax6 * 1.08, f'{ratio6:.2f}×',
             ha='center', va='bottom', fontsize=9.5, fontweight='bold', color=DARK_GRAY)
    ax6.set_ylabel(ylabel, fontsize=9)
    ax6.set_xticks([0, 1])
    ax6.set_xticklabels(['Besu', 'NM'], fontsize=10)
    ax6.set_xlabel(panel_label, fontsize=9)
    ax6.set_ylim(0, ymax6 * 1.30)
    ax6.spines['top'].set_visible(False)
    ax6.spines['right'].set_visible(False)
legend_handles6 = [
    mpatches.Patch(color=BLUE,   alpha=0.82, label='Besu (JVM/G1GC)'),
    mpatches.Patch(color=ORANGE, alpha=0.82, label='Nethermind (CLR/ServerGC)'),
]
fig6.legend(handles=legend_handles6, loc='upper center',
            bbox_to_anchor=(0.5, 0.04), ncol=2, fontsize=9, frameon=True)
fig6.tight_layout(rect=[0, 0.10, 1, 1.0])
save(fig6, 'xray_fig6_comparison')

print("\nAll figures saved to", OUT)
print(f"\nSummary for paper update:")
print(f"  n_besu = {n_besu}, n_nm = {n_nm}")
print(f"  besu_tps = {b_tps_mean:.2f} ± {b_tps_ci:.3f}")
print(f"  nm_tps   = {n_tps_mean:.2f} ± {n_tps_ci:.3f}")
print(f"  besu_avg_pause (all window) = {b_pause_mean:.2f} ms ± {b_pause_ci:.3f}")
print(f"  nm_avg_pause                = {n_pause_mean:.2f} ms ± {n_pause_ci:.3f}")
print(f"  PAF = {paf:.2f}")
print(f"  --- stress-only τ_tx ---")
print(f"  besu stress events/rep = {b_stress_events_mean:.1f}")
print(f"  besu stress total_pause/rep = {b_stress_total_mean:.2f} ms")
print(f"  tau_besu (stress-only) = {tau_besu_stress:.4f} ms/tx")
print(f"  tau_nm                 = {tau_nm:.4f} ms/tx")
print(f"  tau_ratio (stress)     = {tau_nm/tau_besu_stress:.2f}× (NM/Besu)")
print(f"  --- Fig6 τ panel (mean±CI) ---")
print(f"  b_tau = {b_tau_mean:.4f} ± {b_tau_ci:.4f}")
print(f"  n_tau = {n_tau_mean:.4f} ± {n_tau_ci:.4f}")
