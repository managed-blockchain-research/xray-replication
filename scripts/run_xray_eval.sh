#!/bin/bash
# ============================================================
# XRAY Master Orchestrator
#
# Runs Besu then Nethermind XRAY pathology evaluation
# sequentially (never simultaneously — resource safety).
#
# Usage:
#   ./scripts/run_xray_eval.sh           # both clients
#   ./scripts/run_xray_eval.sh besu      # Besu only
#   ./scripts/run_xray_eval.sh nm        # Nethermind only
#
# Estimated runtime:
#   Besu  3 reps: ~25 min
#   NM    3 reps: ~30 min
#   Total:        ~60 min (including cool-down)
# ============================================================
set -e
cd /home/yeochan.yoon/caliper-stress-test

TARGET="${1:-both}"

echo "======================================================================"
echo "XRAY Pathology Evaluation | $(date '+%Y-%m-%d %H:%M:%S')"
echo "Target: ${TARGET}"
echo "======================================================================"

# Ensure no leftover clients
pkill -9 -f "hyperledger.besu.Besu" 2>/dev/null || true
pkill -9 -f "nethermind.dll"         2>/dev/null || true
fuser -k 8545/tcp 8546/tcp 30303/tcp 2>/dev/null || true
sleep 5

check_resources() {
    local free_gb
    free_gb=$(free -g | awk '/^Mem:/{print $7}')
    if [ "${free_gb:-0}" -lt 8 ]; then
        echo "WARNING: Only ${free_gb} GB free RAM. Recommend ≥8 GB before starting."
        echo "Press Ctrl+C to abort, or wait 10 s to continue..."
        sleep 10
    else
        echo "  Available RAM: ${free_gb} GB — OK"
    fi
}

check_resources

EVAL_START=$(date +%s)

# ── Besu ──────────────────────────────────────────────────────────────────────
if [ "${TARGET}" = "both" ] || [ "${TARGET}" = "besu" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "PHASE 1/2: Besu JVM/G1GC"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    bash scripts/run_xray_besu.sh
    echo ""
    echo "  Cooling down 30 s before Nethermind..."
    sleep 30
fi

# ── Nethermind ────────────────────────────────────────────────────────────────
if [ "${TARGET}" = "both" ] || [ "${TARGET}" = "nm" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "PHASE 2/2: Nethermind CLR"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    bash scripts/run_xray_nm.sh
fi

EVAL_END=$(date +%s)
echo ""
echo "======================================================================"
echo "XRAY COMPLETE | Total elapsed: $(( (EVAL_END - EVAL_START) / 60 )) min"
echo "Results: results/xray/"
echo ""
echo "Key metrics to compare (from caliper_console.log | stress row):"
echo "  Besu:  Avg TPS ≈ 50, Avg Latency ≈ 1–2 s, Send Rate ≈ 50"
echo "  NM:    Avg TPS ≈ 5,  Avg Latency ≈ 4–10 s (latency cliff)"
echo ""
echo "GC artifacts:"
echo "  Besu:  gc.log — G1GC Young-Gen sawtooth, Old-Gen 0 MB"
echo "  NM:    gc_trace.nettrace / gc_summary.txt — minimal CLR GC events"
echo "         nm_console.log — search 'Pruning' for RocksDB flush timing"
echo "======================================================================"
