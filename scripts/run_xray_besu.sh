#!/bin/bash
# ============================================================
# XRAY Pathology Study — Hyperledger Besu @ 4 GB Heap
#
# Replicates XRAY Phase 2 using Caliper.
# Shows G1GC Young-Gen sawtooth (Old-Gen stays 0) under
# 200-slot StateBloater stress at 50 TPS with 20 workers.
#
# 3 replications × (30s warmup + 120s measure) ≈ 25 min total
# Output: results/xray/besu/<RUN_ID>/
# ============================================================
set -e
cd /home/yeochan.yoon/caliper-stress-test

BESU_BIN="/home/yeochan.yoon/besu-24.1.1/bin/besu"
LOG4J_CONFIG="/home/yeochan.yoon/caliper-stress-test/log4j2-console.xml"
BENCHCONFIG="benchconfig-xray-besu.yaml"
NETWORKCONFIG="networkconfig_xray_besu.json"
HEAP="4g"
REPLICATIONS=3

RUN_ID=$(date +%Y%m%d_%H%M%S)_xray_besu
RESULTS_DIR="/home/yeochan.yoon/caliper-stress-test/results/xray/besu/${RUN_ID}"
mkdir -p "${RESULTS_DIR}"

echo "======================================================================"
echo "XRAY Besu Pathology | Heap=${HEAP} | 50 TPS | 20 workers | 3 reps"
echo "Run ID: ${RUN_ID}"
echo "Results: ${RESULTS_DIR}"
echo "======================================================================"

wait_for_rpc() {
    local max_wait=120
    local count=0
    echo -n "  Waiting for RPC"
    while [ ${count} -lt ${max_wait} ]; do
        if curl -s --max-time 2 -X POST -H "Content-Type: application/json" \
            --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
            http://localhost:8545 > /dev/null 2>&1; then
            echo " READY"
            return 0
        fi
        echo -n "."
        sleep 1
        count=$((count + 1))
    done
    echo " TIMEOUT"
    return 1
}

stop_besu() {
    local besu_pid="$1"
    kill "${besu_pid}" 2>/dev/null || true
    local w=0
    while kill -0 "${besu_pid}" 2>/dev/null && [ ${w} -lt 30 ]; do
        sleep 1; w=$((w+1))
    done
    kill -9 "${besu_pid}" 2>/dev/null || true
    pkill -9 -f "hyperledger.besu.Besu" 2>/dev/null || true
    fuser -k 8545/tcp 8546/tcp 30303/tcp 2>/dev/null || true
    sleep 5
}

run_single() {
    local rep="$1"
    local label="besu_${rep}"
    local run_dir="${RESULTS_DIR}/${label}"
    mkdir -p "${run_dir}"

    local data_dir="/home/yeochan.yoon/caliper-stress-test/data_xray_${label}_${RUN_ID}"
    local gc_log="${run_dir}/gc.log"

    echo ""
    echo "────────────────────────────────────────────────────────────────"
    echo "RUN: ${label} | $(date '+%Y-%m-%d %H:%M:%S')"
    echo "────────────────────────────────────────────────────────────────"

    pkill -9 -f "hyperledger.besu.Besu" 2>/dev/null || true
    fuser -k 8545/tcp 8546/tcp 30303/tcp 2>/dev/null || true
    sleep 5

    rm -rf "${data_dir}"
    mkdir -p "${data_dir}"

    local java_opts="-Xms${HEAP} -Xmx${HEAP} \
-XX:+UseG1GC \
-XX:MaxGCPauseMillis=200 \
-XX:G1HeapRegionSize=32m \
-Xlog:gc*=info:file=${gc_log}:time,uptime,level,tags:filecount=3,filesize=20M \
-XX:+HeapDumpOnOutOfMemoryError \
-XX:HeapDumpPath=${run_dir}/heap_dump.hprof"

    if [ -f "${LOG4J_CONFIG}" ]; then
        java_opts="${java_opts} -Dlog4j.configurationFile=${LOG4J_CONFIG}"
    fi

    export BESU_OPTS="${java_opts}"

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
        > "${run_dir}/besu_console.log" 2>&1 &
    local besu_pid=$!
    echo "  Besu PID: ${besu_pid}"

    sleep 8
    if ! kill -0 ${besu_pid} 2>/dev/null; then
        echo "  ERROR: Besu died at startup"; tail -20 "${run_dir}/besu_console.log"
        echo "failed=startup" > "${run_dir}/FAILED"; return 1
    fi

    wait_for_rpc || { stop_besu "${besu_pid}"; echo "failed=rpc_timeout" > "${run_dir}/FAILED"; return 1; }

    echo "  Deploying StateBloater contract..."
    python3 deploy_xray_besu.py > "${run_dir}/deploy.log" 2>&1
    if ! grep -q "Contract Address:" "${run_dir}/deploy.log" 2>/dev/null; then
        echo "  ERROR: Deploy failed"; cat "${run_dir}/deploy.log"
        stop_besu "${besu_pid}"; echo "failed=deploy" > "${run_dir}/FAILED"; return 1
    fi
    echo "  Contract deployed: $(grep 'Contract Address:' "${run_dir}/deploy.log" | awk '{print $3}')"

    sleep 3

    echo "  Running Caliper (30s warmup + 120s stress @ 15 TPS)..."
    local t_start=$(date +%s)

    # 250s = 30s warmup + 5s inter-round + 120s stress + 95s buffer
    # Caliper may hang after round ends (known worker-hang bug); kill and extract from logs
    timeout 250 npx caliper launch manager \
        --caliper-workspace ./ \
        --caliper-benchconfig "${BENCHCONFIG}" \
        --caliper-networkconfig "${NETWORKCONFIG}" \
        > "${run_dir}/caliper_console.log" 2>&1
    local caliper_exit=$?
    local t_end=$(date +%s)
    echo "  Caliper exit: ${caliper_exit}. Elapsed: $((t_end - t_start))s"

    cp caliper.log "${run_dir}/caliper.log" 2>/dev/null || true
    cp report.html "${run_dir}/report.html" 2>/dev/null || true

    echo "  Stopping Besu..."
    stop_besu "${besu_pid}"
    rm -rf "${data_dir}"

    # Extract stress round metrics from observer log (works even if Caliper was killed)
    local stress_submitted stress_succ stress_fail
    # Use the last observer line BEFORE the round reset (highest Submitted count)
    local max_line
    max_line=$(grep "\[stress Round 1 Transaction Info\]" "${run_dir}/caliper_console.log" \
        | awk -F'Submitted: ' '{print $2}' | awk '{print $1, NR}' \
        | sort -n | tail -1 | awk '{print $2}')
    local peak_stress_line
    if [ -n "${max_line}" ]; then
        peak_stress_line=$(grep "\[stress Round 1 Transaction Info\]" "${run_dir}/caliper_console.log" \
            | sed -n "${max_line}p")
        stress_submitted=$(echo "${peak_stress_line}" | grep -oP 'Submitted: \K[0-9]+')
        stress_succ=$(echo "${peak_stress_line}" | grep -oP 'Succ: \K[0-9]+')
        stress_fail=$(echo "${peak_stress_line}" | grep -oP 'Fail:\K[0-9]+')
        local stress_tps=$(echo "scale=2; ${stress_succ:-0} / 120" | bc 2>/dev/null || echo "?")
        echo "  Stress (extracted): Submitted=${stress_submitted} Succ=${stress_succ} Fail=${stress_fail} TPS≈${stress_tps}"
        echo "stress_submitted=${stress_submitted}" > "${run_dir}/metrics.txt"
        echo "stress_succ=${stress_succ}" >> "${run_dir}/metrics.txt"
        echo "stress_fail=${stress_fail}" >> "${run_dir}/metrics.txt"
        echo "stress_tps=${stress_tps}" >> "${run_dir}/metrics.txt"
    fi

    # Extract GC summary from gc.log
    local gc_pauses
    gc_pauses=$(grep "Pause Young" "${run_dir}/gc.log" 2>/dev/null | wc -l)
    local gc_last
    gc_last=$(grep "Pause Young" "${run_dir}/gc.log" 2>/dev/null | tail -3)
    echo "  GC Young pauses: ${gc_pauses}"
    echo "${gc_last}"

    echo "  ✓ ${label} complete"
}

# ── Provenance ─────────────────────────────────────────────────────────────────
cat > "${RESULTS_DIR}/provenance.txt" <<EOF
XRAY Pathology Study — Hyperledger Besu @ 4 GB Heap
=====================================================
Run ID:  ${RUN_ID}
Date:    $(date)
Host:    $(hostname)
Binary:  ${BESU_BIN}

Heap:    -Xms4g -Xmx4g
GC:      G1GC, G1HeapRegionSize=32m, MaxGCPauseMillis=200
Load:    50 TPS, 20 workers, 200 slots/tx, 30s warmup + 120s stress
Client:  Besu dev-mode (chainId 1337)
Reps:    ${REPLICATIONS}

Expected pathology:
  G1GC Young-Gen sawtooth at ~158 MB/s fill rate.
  Old Gen remains 0 MB — no Full GC. Demonstrates JVM Scale-Up strategy.
EOF

# ── Pre-flight ─────────────────────────────────────────────────────────────────
pkill -9 -f "hyperledger.besu.Besu" 2>/dev/null || true
fuser -k 8545/tcp 8546/tcp 30303/tcp 2>/dev/null || true
sleep 3

# ── Runs ───────────────────────────────────────────────────────────────────────
for i in $(seq 1 ${REPLICATIONS}); do
    run_single "${i}" || echo "  WARNING: rep ${i} failed, continuing"
    [ "${i}" -lt "${REPLICATIONS}" ] && sleep 20
done

echo ""
echo "======================================================================"
echo "XRAY Besu complete. Results: ${RESULTS_DIR}"
echo "======================================================================"
