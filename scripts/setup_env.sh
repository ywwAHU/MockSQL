#!/usr/bin/env bash
# ==============================================================================
# MockSQL: 一键环境搭建脚本 (Linux)
# 在你的 Linux 实验服务器上运行此脚本即可准备好所有依赖
# ==============================================================================

set -euo pipefail

echo "============================================="
echo "  MockSQL 实验环境搭建"
echo "============================================="

# ---- 1. Python 环境 ----
echo ""
echo "[1/6] 检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: 需要 Python 3.10+"
    exit 1
fi
python3 --version

echo "  创建虚拟环境..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

echo "  安装 MockSQL 和所有依赖..."
pip install -e ".[dev,bench,mysql]"

# ---- 2. PostgreSQL ----
echo ""
echo "[2/6] 检查 PostgreSQL..."
if ! command -v psql &>/dev/null; then
    echo "  安装 PostgreSQL 16..."
    # Ubuntu/Debian
    if command -v apt &>/dev/null; then
        sudo apt update
        sudo apt install -y postgresql-16 postgresql-client-16
    # CentOS/RHEL
    elif command -v yum &>/dev/null; then
        sudo yum install -y postgresql16-server postgresql16
        sudo postgresql-setup --initdb
    fi
    sudo systemctl start postgresql
    sudo systemctl enable postgresql
fi
psql --version

echo "  创建数据库..."
sudo -u postgres psql -c "CREATE USER mocksql WITH PASSWORD 'mocksql123';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE mocksql_bench OWNER mocksql;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE mocksql_bench TO mocksql;" 2>/dev/null || true

export MOCKSQL_DSN="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench"
echo "  DSN: $MOCKSQL_DSN"

# ---- 3. TPC-H 数据生成 ----
echo ""
echo "[3/6] 准备 TPC-H 数据 (SF=10, ~10GB)..."
if [ ! -d "./dbgen" ]; then
    echo "  下载并编译 dbgen..."
    git clone https://github.com/electrum/tpch-dbgen.git dbgen
    cd dbgen
    make
    cd ..
fi

echo "  生成 TPC-H 数据..."
cd dbgen
./dbgen -s 10 -f
cd ..

echo "  加载数据到 PostgreSQL..."
python3 scripts/db_setup.py "$MOCKSQL_DSN" --sf=10 --dbgen-path=./dbgen

# ---- 4. 下载 Spider 和 BIRD 数据集 ----
echo ""
echo "[4/6] 下载基准测试数据集..."
mkdir -p data

# Spider
if [ ! -d "data/spider" ]; then
    echo "  下载 Spider 数据集..."
    wget -q https://drive.google.com/uc?export=download\&id=1iRDVHLr6THdbL2wLzXmGoWJ8F5PrgWkj -O data/spider.zip || {
        echo "  !! 自动下载失败，请手动下载 Spider:"
        echo "     https://yale-lily.github.io/spider"
        echo "     解压到 data/spider/"
    }
    if [ -f "data/spider.zip" ]; then
        unzip -q data/spider.zip -d data/
        rm data/spider.zip
    fi
else
    echo "  Spider 已存在"
fi

# BIRD
if [ ! -d "data/bird" ]; then
    echo "  下载 BIRD 数据集..."
    echo "  !! BIRD 需要手动下载:"
    echo "     https://bird-bench.github.io/"
    echo "     解压到 data/bird/"
else
    echo "  BIRD 已存在"
fi

# ---- 5. 设置 API Keys ----
echo ""
echo "[5/6] LLM API Keys 配置..."
echo "  请在 .env 文件中设置以下环境变量:"
cat > .env.template << 'EOF'
# LLM API Keys (取消注释并填入你的 key)
# export OPENAI_API_KEY="sk-..."
# export ANTHROPIC_API_KEY="sk-ant-..."
# export DEEPSEEK_API_KEY="sk-..."
# export QWEN_API_KEY="sk-..."

# 数据库连接
export MOCKSQL_DSN="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench"

# 数据集路径
export SPIDER_PATH="./data/spider"
export BIRD_PATH="./data/bird"
export TPCH_DBGEN="./dbgen"
EOF

if [ ! -f ".env" ]; then
    cp .env.template .env
    echo "  已创建 .env 模板文件，请编辑填入你的 API keys"
else
    echo "  .env 已存在"
fi

# ---- 6. 验证安装 ----
echo ""
echo "[6/6] 运行基础测试验证安装..."
python3 -m pytest tests/test_sql_parser.py tests/test_synthesizer.py tests/test_feedback.py -v

echo ""
echo "============================================="
echo "  环境搭建完成！"
echo "============================================="
echo ""
echo "下一步操作:"
echo ""
echo "  1. 编辑 .env 文件，填入 LLM API keys"
echo "  2. source .env"
echo "  3. 运行所有实验:"
echo "     bash scripts/run_all.sh"
echo ""
echo "  或者分步运行单个实验:"
echo "     python -m benchmarks.exp1_hazard_detection \$MOCKSQL_DSN"
echo "     python -m benchmarks.exp3_false_positives \$MOCKSQL_DSN"
echo "     python -m benchmarks.exp4_latency \$MOCKSQL_DSN"
echo "     python -m benchmarks.exp5_ablation \$MOCKSQL_DSN"
echo "     python -m benchmarks.exp2_accuracy \$MOCKSQL_DSN spider ./data/spider"
echo ""
echo "  生成论文图表:"
echo "     python -m benchmarks.generate_figures"
echo ""
