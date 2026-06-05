#!/bin/bash
# ============================================================
# XRAY Pathology Study — Nethermind CLR @ spaceneth (disk-based)
#
# Replicates XRAY Phase 2 using Caliper.
# Shows NethDev block-packing ceiling (~5 tx/s) and
# RocksDB dirty-cache flush stalls (~662 ms / 195 MB).
# Identical 50 TPS load as Besu → latency cliff emerges.
#
# 3 replications × (30s warmup + 120s measure) ≈ 30 min total
# dotnet-trace captures CLR GC events per run.
# Output: results/xray/nm/<RUN_ID>/
# ============================================================
set -e
cd /home/yeochan.yoon/caliper-stress-test

NM_DLL="/home/yeochan.yoon/nethermind/src/Nethermind/artifacts/bin/Nethermind.Runner/release/nethermind.dll"
DOTNET_BIN="/home/yeochan.yoon/.dotnet/dotnet"
DT_BIN="${HOME}/.dotnet/tools/dotnet-trace"
NM_CFG="/home/yeochan.yoon/caliper-stress-test/nethermind-caliper-config/caliper_nethdev_cfg.json"

BENCHCONFIG="benchconfig-xray-nm.yaml"
NETWORKCONFIG="networkconfig_xray_nm.json"
REPLICATIONS=3

export DOTNET_ROOT="/home/yeochan.yoon/.dotnet"
export PATH="${DOTNET_ROOT}:${PATH}:${HOME}/.dotnet/tools"

RUN_ID=$(date +%Y%m%d_%H%M%S)_xray_nm
RESULTS_DIR="/home/yeochan.yoon/caliper-stress-test/results/xray/nm/${RUN_ID}"
mkdir -p "${RESULTS_DIR}"

echo "======================================================================"
echo "XRAY Nethermind Pathology | 50 TPS | 20 workers | 3 reps"
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

stop_nm() {
    local nm_pid="$1"
    kill "${nm_pid}" 2>/dev/null || true
    local w=0
    while kill -0 "${nm_pid}" 2>/dev/null && [ ${w} -lt 30 ]; do
        sleep 1; w=$((w+1))
    done
    kill -9 "${nm_pid}" 2>/dev/null || true
    pkill -9 -f "nethermind.dll" 2>/dev/null || true
    fuser -k 8545/tcp 8546/tcp 2>/dev/null || true
    sleep 5
}

run_single() {
    local rep="$1"
    local label="nm_${rep}"
    local run_dir="${RESULTS_DIR}/${label}"
    mkdir -p "${run_dir}"

    local data_dir="/home/yeochan.yoon/caliper-stress-test/data_xray_${label}_${RUN_ID}"

    echo ""
    echo "────────────────────────────────────────────────────────────────"
    echo "RUN: ${label} | $(date '+%Y-%m-%d %H:%M:%S')"
    echo "────────────────────────────────────────────────────────────────"

    pkill -9 -f "nethermind.dll" 2>/dev/null || true
    fuser -k 8545/tcp 8546/tcp 2>/dev/null || true
    sleep 5

    rm -rf "${data_dir}"
    mkdir -p "${data_dir}"

    export DOTNET_EnableDiagnostics=1

    nohup "${DOTNET_BIN}" "${NM_DLL}" \
        --config "${NM_CFG}" \
        --Init.BaseDbPath "${data_dir}" \
        --Blocks.MinGasPrice 0 \
        > "${run_dir}/nm_console.log" 2>&1 &
    local nm_pid=$!
    echo "  Nethermind PID: ${nm_pid}"

    sleep 10
    if ! kill -0 ${nm_pid} 2>/dev/null; then
        echo "  ERROR: Nethermind died at startup"; tail -20 "${run_dir}/nm_console.log"
        echo "failed=startup" > "${run_dir}/FAILED"; return 1
    fi

    wait_for_rpc || { stop_nm "${nm_pid}"; echo "failed=rpc_timeout" > "${run_dir}/FAILED"; return 1; }

    echo "  Deploying StateBloater contract..."
    node deploy_xray_nm.js > "${run_dir}/deploy.log" 2>&1
    if ! grep -q "Contract Address:" "${run_dir}/deploy.log" 2>/dev/null; then
        echo "  ERROR: Deploy failed"; cat "${run_dir}/deploy.log"
        stop_nm "${nm_pid}"; echo "failed=deploy" > "${run_dir}/FAILED"; return 1
    fi
    echo "  Contract deployed: $(grep 'Contract Address:' "${run_dir}/deploy.log" | awk '{print $3}')"

    sleep 5

    # Start dotnet-trace GC collection
    local nettrace_file="${run_dir}/gc_trace.nettrace"
    local dotnet_trace_pid=""
    if [ -f "${DT_BIN}" ]; then
        "${DT_BIN}" collect \
            --process-id "${nm_pid}" \
            --providers "Microsoft-Windows-DotNETRuntime:0x1:5" \
            --output "${nettrace_file}" \
            > "${run_dir}/dotnet_trace.log" 2>&1 &
        dotnet_trace_pid=$!
        echo "  dotnet-trace PID: ${dotnet_trace_pid}"
    else
        echo "  WARNING: dotnet-trace not found — GC trace unavailable"
    fi

    echo "  Running Caliper (30s warmup + 120s stress @ 15 TPS)..."
    local t_start=$(date +%s)

    # 250s = 30s warmup + 5s inter-round + 120s stress + 95s buffer
    # NM NethDev limits to ~5 tx/s; mempool fills; Caliper hangs at round end — kill and extract
    timeout 250 npx caliper launch manager \
        --caliper-workspace ./ \
        --caliper-benchconfig "${BENCHCONFIG}" \
        --caliper-networkconfig "${NETWORKCONFIG}" \
        > "${run_dir}/caliper_console.log" 2>&1
    local caliper_exit=$?
    local t_end=$(date +%s)
    echo "  Caliper exit: ${caliper_exit}. Elapsed: $((t_end - t_start))s"

    # Stop dotnet-trace
    if [ -n "${dotnet_trace_pid}" ] && kill -0 "${dotnet_trace_pid}" 2>/dev/null; then
        kill -INT "${dotnet_trace_pid}" 2>/dev/null || true
        sleep 5
        kill "${dotnet_trace_pid}" 2>/dev/null || true
    fi

    cp caliper.log "${run_dir}/caliper.log" 2>/dev/null || true
    cp report.html "${run_dir}/report.html" 2>/dev/null || true

    echo "  Stopping Nethermind..."
    stop_nm "${nm_pid}"
    rm -rf "${data_dir}"

    # Extract stress round metrics from observer log
    local max_line
    max_line=$(grep "\[stress Round 1 Transaction Info\]" "${run_dir}/caliper_console.log" \
        | awk -F'Submitted: ' '{print $2}' | awk '{print $1, NR}' \
        | sort -n | tail -1 | awk '{print $2}')
    if [ -n "${max_line}" ]; then
        local peak_stress_line
        peak_stress_line=$(grep "\[stress Round 1 Transaction Info\]" "${run_dir}/caliper_console.log" \
            | sed -n "${max_line}p")
        local stress_submitted stress_succ stress_fail
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

    # Parse GC trace if available
    local GC_PARSER="/home/yeochan.yoon/caliper-stress-test/gc-collector/publish/NettraceGcParser.dll"
    if [ -f "${nettrace_file}" ] && [ -f "${GC_PARSER}" ]; then
        echo "  Parsing GC trace..."
        "${DOTNET_BIN}" "${GC_PARSER}" "${nettrace_file}" 2>/dev/null \
            | tee "${run_dir}/gc_summary.txt" | sed 's/^/    /'
    fi

    # Extract CLR stall markers from NM console log
    local stall_count
    stall_count=$(grep -c -i "GC\|stall\|pause\|Slow\|dirty" "${run_dir}/nm_console.log" 2>/dev/null || echo 0)
    echo "  NM GC/stall markers in console: ${stall_count}"
    grep -i "Slow main loop\|GC.*pause\|stall" "${run_dir}/nm_console.log" 2>/dev/null | tail -5 || true

    echo "  ✓ ${label} complete"
}

# ── Provenance ─────────────────────────────────────────────────────────────────
cat > "${RESULTS_DIR}/provenance.txt" <<EOF
XRAY Pathology Study — Nethermind CLR @ spaceneth
==================================================
Run ID:  ${RUN_ID}
Date:    $(date)
Host:    $(hostname)
Binary:  ${NM_DLL}

GC:      CLR default (no heap hard limit override)
Load:    50 TPS, 20 workers, 200 slots/tx, 30s warmup + 120s stress
Client:  Nethermind spaceneth NethDev (chainId 99, disk-based RocksDB)
Reps:    ${REPLICATIONS}

Expected pathologies:
  1. NethDev block-packing ceiling: ~5 tx/s regardless of gas config.
     50 tx/s input → 45 tx/s mempool accumulation → latency cliff (4–10 s).
  2. RocksDB dirty-cache flush stall: ~662 ms per 195 MB dirty threshold.
     Appears as periodic I/O-induced STW in NM console log.
EOF

# ── Pre-flight ─────────────────────────────────────────────────────────────────
pkill -9 -f "nethermind.dll" 2>/dev/null || true
fuser -k 8545/tcp 8546/tcp 2>/dev/null || true
sleep 3

# ── Runs ───────────────────────────────────────────────────────────────────────
for i in $(seq 1 ${REPLICATIONS}); do
    run_single "${i}" || echo "  WARNING: rep ${i} failed, continuing"
    [ "${i}" -lt "${REPLICATIONS}" ] && sleep 20
done

echo ""
echo "======================================================================"
echo "XRAY Nethermind complete. Results: ${RESULTS_DIR}"
echo "======================================================================"
