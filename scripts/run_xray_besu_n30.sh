#!/bin/bash
# ============================================================
# XRAY-v2: Hyperledger Besu, n=30 (SIGMETRICS revision)
#
# Changes vs original XRAY (n=3):
#   - Replications: 30 (CI narrowing, statistical validity)
#   - GC parsing: total_pause_ms, avg_pause_ms, gen0_count
#
# Workload: StateBloater(200 slots), 15 tx/s, 20 workers
#           30s warmup + 120s stress (identical to original XRAY)
#
# Run (schedule via `at` at 23:00 KST):
#   nohup bash scripts/run_xray_besu_n30.sh > /tmp/xray_besu_n30.log 2>&1 &
# ============================================================
set -euo pipefail
cd /home/yeochan.yoon/caliper-stress-test

BESU_BIN="/home/yeochan.yoon/besu-24.1.1/bin/besu"
LOG4J_CONFIG="/home/yeochan.yoon/caliper-stress-test/log4j2-console.xml"
BENCHCONFIG="benchconfig-xray-besu.yaml"
NETWORKCONFIG="networkconfig_xray_besu.json"
HEAP="4g"
REPLICATIONS="${1:-30}"

RUN_ID="$(date +%Y%m%d_%H%M%S)_xray_besu_n${REPLICATIONS}"
BASE_RESULTS="/home/yeochan.yoon/banning/experiments/xray/results/besu_n30/${RUN_ID}"
mkdir -p "${BASE_RESULTS}"

WARMUP_S=30
STRESS_S=120
BOOT_WAIT=10
COOLDOWN=10

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Helpers ────────────────────────────────────────────────────────────────
wait_rpc() {
    local max_wait=120 elapsed=0
    while ! curl -sf -X POST http://localhost:8545 \
            -H 'Content-Type: application/json' \
            -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
            > /dev/null 2>&1; do
        sleep 2; elapsed=$((elapsed + 2))
        if (( elapsed > max_wait )); then log "ERROR: Besu RPC not ready after ${max_wait}s"; return 1; fi
    done
    log "Besu RPC ready (${elapsed}s)"
}

stop_besu() {
    pkill -f "hyperledger.besu.Besu" 2>/dev/null || true
    fuser -k 8545/tcp 8546/tcp 30303/tcp 2>/dev/null || true
    sleep 4
}

parse_besu_gc() {
    local gc_log="$1"
    # Lines: GC(N) Pause Young (Normal) (G1 Evacuation Pause) 2510M->111M(4096M) 7.17ms
    # Also:  GC(N) Pause Full (System.gc()) 2510M->111M(4096M) 450ms
    local gen0_count fullgc_count total_pause_ms avg_pause_ms
    gen0_count=0; fullgc_count=0; total_pause_ms=0; avg_pause_ms=0

    if [ ! -f "${gc_log}" ]; then echo "gc_log_missing=1"; return; fi

    local tmp_pauses
    tmp_pauses=$(grep -E "Pause Young|Pause Full" "${gc_log}" | grep -v "gc,start" | \
        grep -oP '\d+\.\d+(?=ms)' || true)

    gen0_count=$(grep "Pause Young" "${gc_log}" 2>/dev/null | grep -v "gc,start" | wc -l | tr -d ' \n\r' || echo 0)
    fullgc_count=$(grep "Pause Full" "${gc_log}" 2>/dev/null | grep -v "gc,start" | wc -l | tr -d ' \n\r' || echo 0)
    gen0_count="${gen0_count:-0}"; fullgc_count="${fullgc_count:-0}"

    if [ -n "${tmp_pauses}" ]; then
        total_pause_ms=$(echo "${tmp_pauses}" | python3 -c "
import sys
vals = [float(l.strip()) for l in sys.stdin if l.strip()]
print(f'{sum(vals):.2f}' if vals else '0')
")
        avg_pause_ms=$(echo "${tmp_pauses}" | python3 -c "
import sys
vals = [float(l.strip()) for l in sys.stdin if l.strip()]
print(f'{sum(vals)/len(vals):.2f}' if vals else '0')
")
    fi

    cat <<EOF
gen0_gc_count=${gen0_count}
gen1_gc_count=0
gen2_gc_count=${fullgc_count}
total_gc_count=$((gen0_count + fullgc_count))
total_pause_ms=${total_pause_ms}
avg_pause_ms=${avg_pause_ms}
EOF
}

# ── Provenance ────────────────────────────────────────────────────────────
cat > "${BASE_RESULTS}/provenance.txt" <<EOF
XRAY-v2: Hyperledger Besu n=${REPLICATIONS}
=============================================
Run ID:     ${RUN_ID}
Date:       $(date)
Host:       $(hostname)
Consensus:  dev (instant-seal Clique, chainId 1337)
GC mode:    G1GC, -Xms4g -Xmx4g, G1HeapRegionSize=32m
Workload:   StateBloater(200 slots), 15 tx/s, 20 workers
            ${WARMUP_S}s warmup + ${STRESS_S}s stress

Besu:       ${BESU_BIN}
Caliper:    $(cd /home/yeochan.yoon/caliper-stress-test && npm list @hyperledger/caliper-cli 2>/dev/null | grep caliper-cli || echo unknown)
EOF

stop_besu

log "=== XRAY-v2 Besu n=${REPLICATIONS} | RUN_ID=${RUN_ID} ==="
log "Results: ${BASE_RESULTS}"

all_tps=(); all_succ=(); all_fail=()
all_gen0=(); all_fullgc=(); all_total_gc=()
all_total_pause=(); all_avg_pause=()

for rep in $(seq 1 "${REPLICATIONS}"); do
    rep_dir="${BASE_RESULTS}/besu_${rep}"
    mkdir -p "${rep_dir}"
    data_dir="${rep_dir}/data"
    gc_log="${rep_dir}/gc.log"

    log "--- Rep ${rep}/${REPLICATIONS} ---"
    stop_besu
    rm -rf "${data_dir}"; mkdir -p "${data_dir}"

    local_java_opts="-Xms${HEAP} -Xmx${HEAP} \
-XX:+UseG1GC \
-XX:MaxGCPauseMillis=200 \
-XX:G1HeapRegionSize=32m \
-Xlog:gc*=info:file=${gc_log}:time,uptime,level,tags:filecount=3,filesize=20M"

    if [ -f "${LOG4J_CONFIG}" ]; then
        local_java_opts="${local_java_opts} -Dlog4j.configurationFile=${LOG4J_CONFIG}"
    fi

    export BESU_OPTS="${local_java_opts}"

    nohup "${BESU_BIN}" \
        --network=dev \
        --miner-enabled \
        --miner-coinbase=0xBE0cf996DE312b11990E4BcbBf7Fc156880AcFC8 \
        --data-path="${data_dir}" \
        --rpc-http-enabled \
        --rpc-http-port=8545 \
        --rpc-http-host=0.0.0.0 \
        --rpc-http-cors-origins="*" \
        --rpc-ws-enabled \
        --rpc-ws-port=8546 \
        --host-allowlist="*" \
        --min-gas-price=0 \
        --tx-pool-layer-max-capacity=1000000 \
        --tx-pool-max-prioritized=1000000 \
        --tx-pool-max-future-by-sender=100000 \
        --logging=INFO \
        > "${rep_dir}/besu_console.log" 2>&1 &
    BESU_PID=$!
    log "Besu PID: ${BESU_PID}"

    sleep "${BOOT_WAIT}"
    if ! kill -0 "${BESU_PID}" 2>/dev/null; then
        log "SKIP rep ${rep}: Besu died at startup"
        continue
    fi

    if ! wait_rpc; then
        log "SKIP rep ${rep}: RPC timeout"
        stop_besu
        continue
    fi

    log "Deploying StateBloater..."
    if ! python3 deploy_xray_besu.py > "${rep_dir}/deploy.log" 2>&1; then
        log "SKIP rep ${rep}: deploy failed"
        stop_besu
        continue
    fi
    log "Deployed: $(grep 'Contract Address:' "${rep_dir}/deploy.log" | awk '{print $3}' || echo '?')"

    t_start=$(date +%s)
    timeout $((WARMUP_S + STRESS_S + 90)) npx caliper launch manager \
        --caliper-workspace ./ \
        --caliper-benchconfig "${BENCHCONFIG}" \
        --caliper-networkconfig "${NETWORKCONFIG}" \
        --caliper-txTimeout 60 \
        > "${rep_dir}/caliper_console.log" 2>&1 || true
    t_end=$(date +%s)
    log "Caliper done ($((t_end - t_start))s)"

    stop_besu

    # ── Parse caliper metrics ──────────────────────────────────────────────
    stress_tps="N/A"; stress_succ=0; stress_fail=0
    if [ -f "${rep_dir}/caliper_console.log" ]; then
        # Try final summary table row (| stress | Succ | Fail | Send Rate | ... | Throughput |)
        stress_row=$(grep "^| stress" "${rep_dir}/caliper_console.log" 2>/dev/null | tail -1 || true)
        if [ -n "${stress_row}" ]; then
            stress_succ=$(echo "${stress_row}" | awk -F'|' '{gsub(/ /,"",$3); print $3+0}')
            stress_fail=$(echo "${stress_row}" | awk -F'|' '{gsub(/ /,"",$4); print $4+0}')
        else
            # Fallback: peak observer line (works even when caliper was killed early)
            stress_succ=$(grep "\[stress Round.*Transaction Info\]" "${rep_dir}/caliper_console.log" 2>/dev/null | \
                grep -oP 'Succ: \K[0-9]+' | sort -n | tail -1 || echo 0)
            stress_fail=$(grep "\[stress Round.*Transaction Info\]" "${rep_dir}/caliper_console.log" 2>/dev/null | \
                grep -oP 'Fail: ?\K[0-9]+' | sort -n | tail -1 || echo 0)
        fi
        # TPS = confirmed tx / stress duration (120s) — reliable regardless of caliper log format
        if [ "${stress_succ}" -gt 0 ] 2>/dev/null; then
            stress_tps=$(echo "scale=2; ${stress_succ} / ${STRESS_S}" | bc 2>/dev/null || echo "N/A")
        fi
    fi

    # ── Parse GC log ──────────────────────────────────────────────────────
    gc_stats=$(parse_besu_gc "${gc_log}")
    echo "${gc_stats}" > "${rep_dir}/gc_summary.txt"
    gen0=$(echo "${gc_stats}" | grep gen0_gc_count | cut -d= -f2)
    fullgc=$(echo "${gc_stats}" | grep gen2_gc_count | cut -d= -f2)
    total_gc=$(echo "${gc_stats}" | grep total_gc_count | cut -d= -f2)
    total_pause=$(echo "${gc_stats}" | grep total_pause_ms | cut -d= -f2)
    avg_pause=$(echo "${gc_stats}" | grep avg_pause_ms | cut -d= -f2)

    cat > "${rep_dir}/metrics.txt" <<EOF
stress_tps=${stress_tps}
stress_succ=${stress_succ}
stress_fail=${stress_fail}
EOF
    log "  TPS=${stress_tps} succ=${stress_succ} fail=${stress_fail}"
    log "  GC: gen0=${gen0} fullgc=${fullgc} total=${total_gc} total_pause=${total_pause}ms avg=${avg_pause}ms"

    all_tps+=("${stress_tps}")
    all_succ+=("${stress_succ}")
    all_fail+=("${stress_fail}")
    all_gen0+=("${gen0}")
    all_fullgc+=("${fullgc}")
    all_total_gc+=("${total_gc}")
    all_total_pause+=("${total_pause}")
    all_avg_pause+=("${avg_pause}")

    sleep "${COOLDOWN}"
done

# ── Aggregate summary ──────────────────────────────────────────────────────
log "=== Aggregate Summary (${REPLICATIONS} reps) ==="

python3 - "${BASE_RESULTS}" "${REPLICATIONS}" <<'PYEOF'
import sys, json, os, statistics

base = sys.argv[1]
n = int(sys.argv[2])

tps_vals, succ_vals, fail_vals = [], [], []
gen0_vals, fullgc_vals, total_gc_vals = [], [], []
total_pause_vals, avg_pause_vals = [], []

for i in range(1, n+1):
    m = os.path.join(base, f"besu_{i}", "metrics.txt")
    g = os.path.join(base, f"besu_{i}", "gc_summary.txt")
    if not os.path.exists(m):
        continue
    mp = dict(l.strip().split("=",1) for l in open(m) if "=" in l)
    try:
        v = mp.get("stress_tps","N/A")
        if v != "N/A":
            tps_vals.append(float(v))
        succ_vals.append(int(mp.get("stress_succ", 0)))
        fail_vals.append(int(mp.get("stress_fail", 0)))
    except Exception:
        pass
    if os.path.exists(g):
        gp = dict(l.strip().split("=",1) for l in open(g) if "=" in l)
        try:
            gen0_vals.append(int(gp.get("gen0_gc_count", 0)))
            fullgc_vals.append(int(gp.get("gen2_gc_count", 0)))
            total_gc_vals.append(int(gp.get("total_gc_count", 0)))
            total_pause_vals.append(float(gp.get("total_pause_ms", 0)))
            avg_pause_vals.append(float(gp.get("avg_pause_ms", 0)))
        except Exception:
            pass

def stats(vals):
    if not vals: return "N/A"
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
    "succ_std": statistics.stdev(succ_vals) if len(succ_vals) > 1 else 0,
    "fail_mean": statistics.mean(fail_vals) if fail_vals else 0,
    "gen0_mean": statistics.mean(gen0_vals) if gen0_vals else 0,
    "gen0_std": statistics.stdev(gen0_vals) if len(gen0_vals) > 1 else 0,
    "fullgc_mean": statistics.mean(fullgc_vals) if fullgc_vals else 0,
    "fullgc_std": statistics.stdev(fullgc_vals) if len(fullgc_vals) > 1 else 0,
    "total_gc_mean": statistics.mean(total_gc_vals) if total_gc_vals else 0,
    "total_pause_mean_ms": statistics.mean(total_pause_vals) if total_pause_vals else 0,
    "total_pause_std_ms": statistics.stdev(total_pause_vals) if len(total_pause_vals) > 1 else 0,
    "avg_pause_mean_ms": statistics.mean(avg_pause_vals) if avg_pause_vals else 0,
    "avg_pause_std_ms": statistics.stdev(avg_pause_vals) if len(avg_pause_vals) > 1 else 0,
}

with open(os.path.join(base, "aggregate_summary.json"), "w") as f:
    json.dump(summary, f, indent=2)

print("=" * 60)
print("XRAY-v2 Besu Aggregate Results")
print("=" * 60)
print(f"Completed reps:     {summary['n_reps']}/{n}")
print(f"TPS:                {stats(tps_vals)}")
print(f"Confirmed tx/run:   {stats(succ_vals)}")
print(f"Failed tx/run:      {stats(fail_vals)}")
print()
print(f"GC events/run:      gen0={stats(gen0_vals)}  fullgc={stats(fullgc_vals)}")
print(f"Total GC/run:       {stats(total_gc_vals)}")
print(f"Total pause/run:    {stats(total_pause_vals)} ms")
print(f"Avg pause/event:    {stats(avg_pause_vals)} ms")
if tps_vals and total_pause_vals and succ_vals:
    tau = [p/s for p,s in zip(total_pause_vals, succ_vals) if s > 0]
    print(f"tau_tx (ms/tx):     {stats(tau)}")
print("=" * 60)
print(f"Full results: {base}/aggregate_summary.json")
PYEOF

log "Pipeline complete. Results: ${BASE_RESULTS}"
