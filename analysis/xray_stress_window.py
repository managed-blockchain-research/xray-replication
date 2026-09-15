#!/usr/bin/env python3
"""
Shared stress-window isolation helpers for the XRAY OFAT sweep + queueing
model analysis. Both Besu and Nethermind pause events are filtered to the
stress-round-only window (matching gen_figures_n30.py's original
methodology for the paper's main Section 5/6 figures), for methodological
consistency across the whole paper.
"""
import datetime
import glob
import os
import re
import statistics as st
import subprocess

SWEEP_ROOT = os.path.expanduser('~/banning/experiments/xray/results/sweep')
DOTNET = '/home/yeochan.yoon/.dotnet/dotnet'
PARSER = os.path.expanduser('~/caliper-stress-test/gc-collector/publish/NettraceGcParser.dll')

# label -> (tps, slots, duration_s, n_target, gas_limit)
CONDITIONS = {
    'anchor_lat':          (15, 200, 120, 30, 8000000),
    'tps5':                (5,  200, 120, 10, 8000000),
    'tps10':               (10, 200, 120, 10, 8000000),
    'tps20':               (20, 200, 120, 10, 8000000),
    'slots50':             (15, 50,  120, 10, 8000000),
    'slots100':            (15, 100, 120, 10, 8000000),
    'slots400':            (15, 400, 120, 10, 12000000),
    'dur300':              (15, 200, 300, 8,  8000000),
    'dur600':              (15, 200, 600, 8,  8000000),
    'diag_20tps_400slots': (20, 400, 120, 3,  12000000),
}

TS_RE = re.compile(r'^(\d{4})\.(\d{2})\.(\d{2})-(\d{2}):(\d{2}):(\d{2})\.(\d{3})')
JVM_START_RE = re.compile(r'\[(\d{4}-\d{2}-\d{2}T[\d:.]+\+\d{4})\]\[([\d.]+)s\]')
UPTIME_RE = re.compile(r'\[([\d.]+)s\]')
PAUSE_RE = re.compile(r'\d+M->\d+M\(\d+M\)\s+([\d.]+)ms')


def find_run_dir(platform, label):
    pattern = os.path.join(SWEEP_ROOT, platform, label, '*')
    target_n = CONDITIONS[label][3]
    candidates = [d for d in glob.glob(pattern) if os.path.isdir(d)]
    for d in sorted(candidates, key=os.path.getmtime, reverse=True):
        gr = os.path.join(d, 'good_reps.txt')
        if os.path.exists(gr) and len(open(gr).read().split()) == target_n:
            return d
    return None


def good_reps(run_dir):
    return [l.strip() for l in open(os.path.join(run_dir, 'good_reps.txt')) if l.strip()]


def parse_caliper_stress_start(caliper_log_path):
    """Return unix timestamp (UTC) of 'Started round 2 (stress)'."""
    with open(caliper_log_path) as f:
        for line in f:
            if 'Started round' not in line:
                continue
            if 'round 2' not in line and 'stress' not in line.lower():
                continue
            m = TS_RE.match(line)
            if m:
                yr, mo, dy, hr, mn, sc, ms = [int(x) for x in m.groups()]
                dt = datetime.datetime(yr, mo, dy, hr, mn, sc, ms * 1000,
                                        tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
                return dt.timestamp()
    return None


def parse_jvm_start_time(gc_log_path):
    with open(gc_log_path) as f:
        for line in f:
            m = JVM_START_RE.match(line)
            if m:
                wall_str, uptime_s = m.group(1), float(m.group(2))
                wall_dt = datetime.datetime.strptime(wall_str, '%Y-%m-%dT%H:%M:%S.%f%z')
                return wall_dt.timestamp() - uptime_s
    return None


def besu_stress_pauses(rep_dir, stress_duration):
    """Returns list of (gen, pause_ms) for stress-round-only Besu GC pauses."""
    gc_log = os.path.join(rep_dir, 'gc.log')
    caliper_log = os.path.join(rep_dir, 'caliper_console.log')
    if not os.path.exists(gc_log):
        return []
    jvm_start = parse_jvm_start_time(gc_log)
    if jvm_start is None:
        return []
    stress_wall = parse_caliper_stress_start(caliper_log) if os.path.exists(caliper_log) else None
    if stress_wall is None:
        return []
    stress_uptime_start = stress_wall - jvm_start
    stress_uptime_end = stress_uptime_start + stress_duration

    pauses = []
    with open(gc_log) as f:
        for line in f:
            if 'gc,start' in line:
                continue
            is_full = 'Pause Full' in line
            if 'Pause Young' not in line and not is_full:
                continue
            um = UPTIME_RE.search(line)
            pm = PAUSE_RE.search(line)
            if um and pm:
                uptime = float(um.group(1))
                if stress_uptime_start <= uptime <= stress_uptime_end:
                    pauses.append(('full' if is_full else 'young', float(pm.group(1))))
    return pauses


def nm_stress_pauses(rep_dir, stress_duration):
    """Returns list of (gen, pause_ms) for stress-round-only NM GC pauses."""
    f = os.path.join(rep_dir, 'gc_trace.nettrace')
    caliper_log = os.path.join(rep_dir, 'caliper_console.log')
    if not os.path.exists(f) or not os.path.exists(caliper_log):
        return []
    stress_wall = parse_caliper_stress_start(caliper_log)
    if stress_wall is None:
        return []
    out = subprocess.run([DOTNET, PARSER, f, '--csv'], capture_output=True, text=True, timeout=60)
    lines = out.stdout.splitlines()
    session_start_utc = None
    events = []  # (gen, pause_ms, suspend_start_ms)
    for line in lines:
        if line.startswith('# session_start_utc='):
            ts = line.split('=', 1)[1]
            session_start_utc = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
            continue
        if line.startswith('gen,'):
            continue
        parts = line.split(',')
        if len(parts) == 3:
            events.append((int(parts[0]), float(parts[1]), float(parts[2])))
    if session_start_utc is None or not events:
        return []
    session_start_unix = session_start_utc.timestamp()
    stress_offset_ms = (stress_wall - session_start_unix) * 1000.0
    stress_end_ms = stress_offset_ms + stress_duration * 1000.0
    return [(gen, p) for gen, p, t in events if stress_offset_ms <= t <= stress_end_ms]


def stress_pauses(platform, rep_dir, stress_duration):
    if platform == 'besu':
        return besu_stress_pauses(rep_dir, stress_duration)
    return nm_stress_pauses(rep_dir, stress_duration)


def latencies(run_dir, reps):
    import csv
    lat = []
    for rep in reps:
        for f in glob.glob(os.path.join(run_dir, rep, 'latency_worker*.csv')):
            with open(f) as fh:
                r = csv.DictReader(fh)
                for row in r:
                    if row['success'] == 'true' and row['latency_ms']:
                        lat.append(int(row['latency_ms']))
    return lat


def moments(vals):
    if not vals:
        return 0.0, 0.0, 0.0
    e1 = st.mean(vals)
    e2 = sum(v * v for v in vals) / len(vals)
    return e1, e2, (e2 / (2 * e1) if e1 > 0 else 0.0)
