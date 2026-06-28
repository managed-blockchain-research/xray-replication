#!/bin/bash
# ============================================================
# XRAY-v2: Nethermind Clique, n=30 (SIGMETRICS revision)
#
# Changes vs original XRAY (n=3, NethDev):
#   - Consensus: Clique PoA (period=1s, no tx/block ceiling)
#   - GC mode: Server GC (DOTNET_gcServer=1)
#   - Replications: 30 (CI narrowing, statistical validity)
#   - chainId: 1337 (Apple-to-Apple with Besu dev)
#
# Workload: StateBloater(200 slots), 15 tx/s, 20 workers
#           30s warmup + 120s stress (identical to original XRAY)
#
# Run (schedule via `at` at 23:00 KST):
#   nohup bash scripts/run_xray_nm_clique_n30.sh > /tmp/xray_nm_clique_n30.log 2>&1 &
# ============================================================
set -euo pipefail
cd /home/yeochan.yoon/caliper-stress-test

DOTNET_BIN="/home/yeochan.yoon/.dotnet/dotnet"
DOTNET_TRACE="/home/yeochan.yoon/.dotnet/tools/dotnet-trace"
GC_PARSER="/home/yeochan.yoon/caliper-stress-test/gc-collector/publish/NettraceGcParser.dll"
LAUNCH_NM="/home/yeochan.yoon/banning/experiments/xray/scripts/launch_nm_clique.sh"
BENCHCONFIG="benchconfig-xray-nm.yaml"
NETWORKCONFIG="networkconfig_xray_nm_clique.json"
DEPLOY_SCRIPT="deploy_xray_nm_clique.js"

REPLICATIONS=30
WARMUP_S=30
STRESS_S=120
NM_BOOT_WAIT=25
TRACE_START_WAIT=5
COOLDOWN=10

RUN_ID="$(date +%Y%m%d_%H%M%S)_xray_nm_clique_n${REPLICATIONS}"
BASE_RESULTS="/home/yeochan.yoon/banning/experiments/xray/results/xray_clique_nm/${RUN_ID}"
mkdir -p "${BASE_RESULTS}"

export DOTNET_ROOT="/home/yeochan.yoon/.dotnet"
export PATH="/home/yeochan.yoon/.dotnet/tools:${DOTNET_ROOT}:${PATH}"
export DOTNET_EnableDiagnostics=1

# ── Helpers ────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')] $*"; }

check_resources() {
    # Waits up to 4 hours in 10-min loops for safe conditions.
    # Aborts only if swap is fully exhausted AND avail < 30GB (OOM imminent).
    local waited=0
    while true; do
        local avail_gb load swap_free_kb
        avail_gb=$(free -g | awk '/^Mem/{print $7}')
        load=$(awk '{print $1}' /proc/loadavg)
        swap_free_kb=$(awk '/^SwapFree/{print $2}' /proc/meminfo)

        local ok=1
        if (( avail_gb < 50 )); then
            log "WAIT: Only ${avail_gb}GB available (need 50GB)"
            ok=0
        fi
        if awk "BEGIN{exit !($load > 20)}"; then
            log "WAIT: Load ${load} > 20 (other experiments running)"
            ok=0
        fi
        if (( swap_free_kb < 102400 )) && (( avail_gb < 30 )); then
            log "ABORT: Swap exhausted AND only ${avail_gb}GB avail — OOM risk"
            exit 1
        fi

        if (( ok )); then
            log "Resources OK: ${avail_gb}GB avail, load=${load}, swap_free=${swap_free_kb}KB"
            return 0
        fi

        waited=$((waited + 600))
        if (( waited > 14400 )); then  # 4 hours max wait
            log "ABORT: Resources not available after 4h wait"
            exit 1
        fi
        log "Waiting 10min for resources to free up... (${waited}s elapsed)"
        sleep 600
    done
}

wait_rpc() {
    local max_wait=60 elapsed=0
    while ! curl -sf -X POST http://localhost:8545 \
            -H 'Content-Type: application/json' \
            -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
            > /dev/null 2>&1; do
        sleep 2; elapsed=$((elapsed+2))
        if (( elapsed > max_wait )); then
            log "ERROR: NM RPC not ready after ${max_wait}s"
            return 1
        fi
    done
    log "NM RPC ready (${elapsed}s)"
}

stop_nm() {
    local pid_file="${1:-/tmp/xray_clique_nm.pid}"
    if [ -f "${pid_file}" ]; then
        local pid; pid=$(cat "${pid_file}")
        kill "${pid}" 2>/dev/null || true
        sleep 3
        kill -9 "${pid}" 2>/dev/null || true
        rm -f "${pid_file}"
    fi
    pkill -f "nethermind.dll" 2>/dev/null || true
    fuser -k 8545/tcp 2>/dev/null || true
    sleep 2
}

# ── Pre-flight ─────────────────────────────────────────────────────────────
check_resources
stop_nm  # ensure clean state

log "=== XRAY-v2 NM Clique n=${REPLICATIONS} | RUN_ID=${RUN_ID} ==="
log "Results: ${BASE_RESULTS}"

# Capture build provenance
cat > "${BASE_RESULTS}/provenance.txt" <<EOF
XRAY-v2: Nethermind Clique n=${REPLICATIONS}
=============================================
Run ID:     ${RUN_ID}
Date:       $(date)
Host:       $(hostname)
Consensus:  Clique PoA (period=1s, chainId=1337)
GC mode:    Server GC (DOTNET_gcServer=1)
Workload:   StateBloater(200 slots), 15 tx/s, 20 workers
            ${WARMUP_S}s warmup + ${STRESS_S}s stress

Nethermind: $(git -C /home/yeochan.yoon/nethermind log --oneline -1 2>/dev/null || echo unknown)
Caliper:    $(cd /home/yeochan.yoon/caliper-stress-test && npm list @hyperledger/caliper-cli 2>/dev/null | grep caliper-cli || echo unknown)
EOF

# ── Aggregate accumulators ─────────────────────────────────────────────────
all_tps=()
all_succ=()
all_fail=()
all_gen0=()
all_gen1=()
all_gen2=()
all_total_gc=()
all_total_pause_ms=()
all_avg_pause_ms=()

# ── Main loop ──────────────────────────────────────────────────────────────
for rep in $(seq 1 "${REPLICATIONS}"); do
    rep_dir="${BASE_RESULTS}/nm_${rep}"
    mkdir -p "${rep_dir}"
    data_dir="${rep_dir}/data"
    pid_file="${rep_dir}/nm.pid"

    log "--- Rep ${rep}/${REPLICATIONS} ---"

    # Start Nethermind with Clique
    bash "${LAUNCH_NM}" "${data_dir}" "${rep_dir}/nm_trace.log" "${pid_file}"
    sleep "${NM_BOOT_WAIT}"

    if ! wait_rpc; then
        log "SKIP rep ${rep}: NM failed to start"
        stop_nm "${pid_file}"
        continue
    fi

    NM_PID=$(cat "${pid_file}" 2>/dev/null || pgrep -f "nethermind.dll" | head -1)
    log "NM PID: ${NM_PID}"

    # Start dotnet-trace (GC events only)
    nettrace_file="${rep_dir}/gc_trace.nettrace"
    "${DOTNET_TRACE}" collect \
        --process-id "${NM_PID}" \
        --providers "Microsoft-Windows-DotNETRuntime:0x1:5" \
        -o "${nettrace_file}" \
        > "${rep_dir}/dotnet_trace.log" 2>&1 &
    TRACE_PID=$!
    sleep "${TRACE_START_WAIT}"

    # Deploy contract
    log "Deploying StateBloater..."
    if ! node "${DEPLOY_SCRIPT}" > "${rep_dir}/deploy.log" 2>&1; then
        log "SKIP rep ${rep}: deploy failed"
        kill "${TRACE_PID}" 2>/dev/null || true
        stop_nm "${pid_file}"
        continue
    fi
    log "Contract deployed"

    # Run caliper
    t_start=$(date +%s)
    timeout $((WARMUP_S + STRESS_S + 60)) npx caliper launch manager \
        --caliper-workspace ./ \
        --caliper-benchconfig "${BENCHCONFIG}" \
        --caliper-networkconfig "${NETWORKCONFIG}" \
        --caliper-txTimeout 60 \
        > "${rep_dir}/caliper_console.log" 2>&1 || true
    t_end=$(date +%s)
    log "Caliper done ($((t_end - t_start))s)"

    # Stop dotnet-trace
    kill "${TRACE_PID}" 2>/dev/null || true
    wait "${TRACE_PID}" 2>/dev/null || true
    sleep 2

    # Copy NM console log
    cp "${data_dir}/nm_console.log" "${rep_dir}/nm_console.log" 2>/dev/null || true

    # Stop NM
    stop_nm "${pid_file}"

    # ── Parse caliper metrics ──────────────────────────────────────────────
    stress_tps="N/A"; stress_succ=0; stress_fail=0
    caliper_log="${rep_dir}/caliper_console.log"
    if [ -f "${caliper_log}" ]; then
        # Try final summary table row (| stress | Succ | Fail | Send Rate | ... | Throughput |)
        stress_row=$(grep "^| stress" "${caliper_log}" 2>/dev/null | tail -1 || true)
        if [ -n "${stress_row}" ]; then
            stress_succ=$(echo "${stress_row}" | awk -F'|' '{gsub(/ /,"",$3); print $3+0}')
            stress_fail=$(echo "${stress_row}" | awk -F'|' '{gsub(/ /,"",$4); print $4+0}')
        else
            # Fallback: peak observer line (works even if caliper was killed early)
            stress_succ=$(grep "\[stress Round.*Transaction Info\]" "${caliper_log}" 2>/dev/null | \
                grep -oP 'Succ: \K[0-9]+' | sort -n | tail -1 || echo 0)
            stress_fail=$(grep "\[stress Round.*Transaction Info\]" "${caliper_log}" 2>/dev/null | \
                grep -oP 'Fail: ?\K[0-9]+' | sort -n | tail -1 || echo 0)
        fi
        # TPS = confirmed tx / stress duration (120s) — reliable regardless of caliper log format
        if [ "${stress_succ}" -gt 0 ] 2>/dev/null; then
            stress_tps=$(echo "scale=2; ${stress_succ} / ${STRESS_S}" | bc 2>/dev/null || echo "N/A")
        fi
    fi
    cat > "${rep_dir}/metrics.txt" <<EOF
stress_tps=${stress_tps}
stress_succ=${stress_succ}
stress_fail=${stress_fail}
EOF
    log "  TPS=${stress_tps} succ=${stress_succ} fail=${stress_fail}"

    # ── Parse GC trace ─────────────────────────────────────────────────────
    gen0=0; gen1=0; gen2=0; total_gc=0; total_pause_ms=0; avg_pause_ms=0
    if [ -f "${nettrace_file}" ] && [ -f "${GC_PARSER}" ]; then
        gc_out=$("${DOTNET_BIN}" "${GC_PARSER}" "${nettrace_file}" 2>/dev/null || echo "")
        echo "${gc_out}" > "${rep_dir}/gc_summary.txt"
        gen0=$(echo "${gc_out}" | grep "gen0_gc_count" | cut -d= -f2 || echo 0)
        gen1=$(echo "${gc_out}" | grep "gen1_gc_count" | cut -d= -f2 || echo 0)
        gen2=$(echo "${gc_out}" | grep "gen2_gc_count" | cut -d= -f2 || echo 0)
        total_gc=$(echo "${gc_out}" | grep "total_gc_count" | cut -d= -f2 || echo 0)
        total_pause_ms=$(echo "${gc_out}" | grep "total_pause_ms" | cut -d= -f2 || echo 0)
        avg_pause_ms=$(echo "${gc_out}" | grep "avg_pause_ms" | cut -d= -f2 || echo 0)
        log "  GC: gen0=${gen0} gen1=${gen1} gen2=${gen2} total=${total_gc} avg_pause=${avg_pause_ms}ms"
    else
        log "  GC: nettrace missing or parser not found"
    fi

    # Accumulate
    all_tps+=("${stress_tps}")
    all_succ+=("${stress_succ}")
    all_fail+=("${stress_fail}")
    all_gen0+=("${gen0}")
    all_gen1+=("${gen1}")
    all_gen2+=("${gen2}")
    all_total_gc+=("${total_gc}")
    all_total_pause_ms+=("${total_pause_ms}")
    all_avg_pause_ms+=("${avg_pause_ms}")

    sleep "${COOLDOWN}"
done

# ── Aggregate summary ──────────────────────────────────────────────────────
log "=== Aggregate Summary (${REPLICATIONS} reps) ==="

python3 - "${BASE_RESULTS}" "${REPLICATIONS}" <<'PYEOF'
import sys, json, os, statistics

base = sys.argv[1]
n = int(sys.argv[2])

tps_vals, succ_vals, fail_vals = [], [], []
gen0_vals, gen1_vals, gen2_vals = [], [], []
total_gc_vals, total_pause_vals, avg_pause_vals = [], [], []
gen2_count_reps = 0

for i in range(1, n+1):
    m = os.path.join(base, f"nm_{i}", "metrics.txt")
    g = os.path.join(base, f"nm_{i}", "gc_summary.txt")
    if not os.path.exists(m):
        continue
    mp = dict(l.strip().split("=") for l in open(m) if "=" in l)
    try:
        tps_vals.append(float(mp.get("stress_tps", 0)))
        succ_vals.append(int(mp.get("stress_succ", 0)))
        fail_vals.append(int(mp.get("stress_fail", 0)))
    except Exception:
        pass
    if os.path.exists(g):
        gp = dict(l.strip().split("=") for l in open(g) if "=" in l)
        try:
            gen0_vals.append(int(gp.get("gen0_gc_count", 0)))
            gen1_vals.append(int(gp.get("gen1_gc_count", 0)))
            gen2_vals.append(int(gp.get("gen2_gc_count", 0)))
            total_gc_vals.append(int(gp.get("total_gc_count", 0)))
            total_pause_vals.append(float(gp.get("total_pause_ms", 0)))
            avg_pause_vals.append(float(gp.get("avg_pause_ms", 0)))
            if int(gp.get("gen2_gc_count", 0)) > 0:
                gen2_count_reps += 1
        except Exception:
            pass

def stats(vals):
    if not vals:
        return "N/A"
    mu = statistics.mean(vals)
    if len(vals) > 1:
        sd = statistics.stdev(vals)
        return f"{mu:.2f} ± {sd:.2f}"
    return f"{mu:.2f}"

summary = {
    "n_reps": len(tps_vals),
    "tps_mean": statistics.mean(tps_vals) if tps_vals else 0,
    "tps_std": statistics.stdev(tps_vals) if len(tps_vals) > 1 else 0,
    "succ_mean": statistics.mean(succ_vals) if succ_vals else 0,
    "fail_mean": statistics.mean(fail_vals) if fail_vals else 0,
    "gen0_mean": statistics.mean(gen0_vals) if gen0_vals else 0,
    "gen1_mean": statistics.mean(gen1_vals) if gen1_vals else 0,
    "gen2_mean": statistics.mean(gen2_vals) if gen2_vals else 0,
    "gen2_reps_pct": (gen2_count_reps / len(gen2_vals) * 100) if gen2_vals else 0,
    "total_gc_mean": statistics.mean(total_gc_vals) if total_gc_vals else 0,
    "total_pause_mean_ms": statistics.mean(total_pause_vals) if total_pause_vals else 0,
    "total_pause_std_ms": statistics.stdev(total_pause_vals) if len(total_pause_vals) > 1 else 0,
    "avg_pause_mean_ms": statistics.mean(avg_pause_vals) if avg_pause_vals else 0,
    "avg_pause_std_ms": statistics.stdev(avg_pause_vals) if len(avg_pause_vals) > 1 else 0,
}

with open(os.path.join(base, "aggregate_summary.json"), "w") as f:
    json.dump(summary, f, indent=2)

print("=" * 60)
print("XRAY-v2 NM Clique Aggregate Results")
print("=" * 60)
print(f"Completed reps:     {summary['n_reps']}/{n}")
print(f"TPS:                {stats(tps_vals)}")
print(f"Confirmed tx/run:   {stats(succ_vals)}")
print(f"Failed tx/run:      {stats(fail_vals)}")
print()
print(f"GC events/run:      gen0={stats(gen0_vals)}  gen1={stats(gen1_vals)}  gen2={stats(gen2_vals)}")
print(f"Total GC/run:       {stats(total_gc_vals)}")
print(f"Reps with gen2:     {gen2_count_reps}/{len(gen2_vals)} ({summary['gen2_reps_pct']:.0f}%)")
print(f"Total pause/run:    {stats(total_pause_vals)} ms")
print(f"Avg pause/event:    {stats(avg_pause_vals)} ms")
if tps_vals and total_pause_vals and succ_vals:
    tau = [p/s for p,s in zip(total_pause_vals, succ_vals) if s > 0]
    print(f"tau_tx (ms/tx):     {stats(tau)}")
print("=" * 60)
print(f"Full results: {base}/aggregate_summary.json")
PYEOF

log "Pipeline complete. Results: ${BASE_RESULTS}"
