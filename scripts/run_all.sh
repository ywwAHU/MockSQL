#!/usr/bin/env bash
# ==============================================================================
# MockSQL: Complete Experiment Pipeline
# Run all experiments for the VLDB paper
# ==============================================================================

set -euo pipefail

# ---- Configuration ----
DSN="${MOCKSQL_DSN:-postgresql://postgres:postgres@localhost:5432/mocksql_bench}"
SPIDER_PATH="${SPIDER_PATH:-./data/spider}"
BIRD_PATH="${BIRD_PATH:-./data/bird}"
TPCH_DBGEN="${TPCH_DBGEN:-./dbgen}"
RESULTS_DIR="./results"
FIGURES_DIR="./paper_figures"

echo "============================================="
echo "  MockSQL Experiment Pipeline"
echo "============================================="
echo "DSN: $DSN"
echo "Results: $RESULTS_DIR"
echo ""

# ---- Step 0: Setup ----
echo "[Step 0] Installing dependencies..."
pip install -e ".[dev,bench]"

echo "[Step 0] Creating results directory..."
mkdir -p "$RESULTS_DIR" "$FIGURES_DIR"

# ---- Step 1: Database Setup ----
echo ""
echo "[Step 1] Setting up TPC-H database..."
# Create the database if it doesn't exist
psql "$DSN" -c "SELECT 1" 2>/dev/null || {
    DB_NAME=$(echo "$DSN" | grep -oP '(?<=/)[^/]+$')
    BASE_DSN=$(echo "$DSN" | sed "s|/$DB_NAME|/postgres|")
    psql "$BASE_DSN" -c "CREATE DATABASE $DB_NAME" 2>/dev/null || true
}

python scripts/db_setup.py "$DSN" --sf=10 --dbgen-path="$TPCH_DBGEN"

# ---- Step 2: Generate Benchmark Queries ----
echo ""
echo "[Step 2] Generating benchmark queries..."
python -m benchmarks.hazard_generator

# ---- Step 3: Run Experiments ----
echo ""
echo "[Step 3] Running Experiment 1: Hazard Detection..."
python -m benchmarks.exp1_hazard_detection "$DSN"

echo ""
echo "[Step 4] Running Experiment 3: False Positive Analysis..."
python -m benchmarks.exp3_false_positives "$DSN"

echo ""
echo "[Step 5] Running Experiment 4: Latency Measurement..."
python -m benchmarks.exp4_latency "$DSN"

echo ""
echo "[Step 6] Running Experiment 5: Ablation Study..."
python -m benchmarks.exp5_ablation "$DSN"

# ---- Step 4: Text-to-SQL Accuracy (requires LLM API keys) ----
if [ -n "${OPENAI_API_KEY:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo ""
    echo "[Step 7] Running Experiment 2: Text-to-SQL Accuracy..."

    if [ -d "$SPIDER_PATH" ]; then
        echo "  Testing on Spider..."
        python -m benchmarks.exp2_accuracy "$DSN" spider "$SPIDER_PATH"
    else
        echo "  Spider dataset not found at $SPIDER_PATH, skipping."
    fi

    if [ -d "$BIRD_PATH" ]; then
        echo "  Testing on BIRD..."
        python -m benchmarks.exp2_accuracy "$DSN" bird "$BIRD_PATH"
    else
        echo "  BIRD dataset not found at $BIRD_PATH, skipping."
    fi
else
    echo ""
    echo "[Step 7] SKIPPED: Text-to-SQL Accuracy (no LLM API keys set)"
    echo "  Set OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, QWEN_API_KEY"
fi

# ---- Step 5: Generate Figures ----
echo ""
echo "[Step 8] Generating paper figures and tables..."
python -m benchmarks.generate_figures

echo ""
echo "============================================="
echo "  All experiments complete!"
echo "  Results: $RESULTS_DIR/"
echo "  Figures: $FIGURES_DIR/"
echo "============================================="
