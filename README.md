# MockSQL: 双通道验证中间件

面向安全 LLM-数据库交互的双通道验证中间件 (VLDB 论文实验代码)

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 20.04 / 22.04 / 24.04 |
| Python | >= 3.10 |
| PostgreSQL | >= 14 (推荐 16) |
| 内存 | >= 16GB (TPC-H SF=10 加载时需要) |
| 磁盘 | >= 30GB (TPC-H SF=10 数据约 10GB + 生成中间文件) |

---

## 目录结构

```
mocksql/
├── mocksql/                    # 核心代码
│   ├── proxy.py                # 主入口：双通道并发验证
│   ├── plan_channel.py         # Plan Channel: 生产库 EXPLAIN 性能风险检测
│   ├── exec_channel.py         # Exec Channel: DuckDB 沙盒执行 + 语义验证
│   ├── synthesizer.py          # Stats-Aware 合成数据生成器
│   ├── metadata.py             # 元数据提取与缓存 (pg_stats)
│   ├── feedback.py             # 反馈生成器 (PASS/WARNING/REJECT)
│   ├── sql_parser.py           # SQL 解析 (提取表名)
│   ├── llm_client.py           # LLM API 调用 (OpenAI/Anthropic/DeepSeek/Qwen)
│   ├── config.py               # 配置类
│   └── cli.py                  # 命令行工具
├── benchmarks/                 # 论文 5 组实验
│   ├── hazard_generator.py     # 生成测试用 SQL (200 条毒 SQL + 800 条正确 SQL)
│   ├── exp1_hazard_detection.py    # 实验1: 安全检出率 (论文 Table 3)
│   ├── exp2_accuracy.py            # 实验2: Text-to-SQL 准确率提升 (论文 Table 4)
│   ├── exp3_false_positives.py     # 实验3: 误报率分析 (论文 Table 5)
│   ├── exp4_latency.py             # 实验4: 延迟开销 (论文 Table 6)
│   ├── exp5_ablation.py            # 实验5: 消融实验 (论文 Table 7)
│   └── generate_figures.py         # 生成 LaTeX 表格 + matplotlib 图表
├── scripts/
│   ├── setup_env.sh            # 一键环境搭建
│   ├── run_all.sh              # 一键跑全部实验
│   └── db_setup.py             # TPC-H 建表与数据加载
├── tests/                      # 单元测试
├── configs/
│   └── default_config.yaml     # 默认配置模板
└── pyproject.toml
```

---

## 第一步：基础环境安装

### 1.1 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git build-essential wget unzip
```

### 1.2 安装 PostgreSQL 16

```bash
# 添加 PostgreSQL 官方源
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt update
sudo apt install -y postgresql-16 postgresql-client-16

# 启动服务
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

验证安装：

```bash
psql --version
# 应输出: psql (PostgreSQL) 16.x
```

### 1.3 创建数据库和用户

```bash
sudo -u postgres psql << 'EOF'
CREATE USER mocksql WITH PASSWORD 'mocksql123';
CREATE DATABASE mocksql_bench OWNER mocksql;
GRANT ALL PRIVILEGES ON DATABASE mocksql_bench TO mocksql;
\q
EOF
```

验证连接：

```bash
psql "postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench" -c "SELECT version();"
```

---

## 第二步：项目安装

### 2.1 上传代码

将 `mocksql/` 文件夹上传到服务器 (scp / rsync / git clone)：

```bash
# 例：从本地 Windows 传到服务器
scp -r mocksql/ user@your-server:/home/user/mocksql

# 进入项目目录
cd /home/user/mocksql
```

### 2.2 创建 Python 虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate

# 升级 pip
pip install --upgrade pip

# 安装 MockSQL (含所有可选依赖)
pip install -e ".[dev,bench]"
```

### 2.3 验证安装

```bash
# 运行不需要 PostgreSQL 的单元测试
python -m pytest tests/test_sql_parser.py tests/test_synthesizer.py tests/test_feedback.py -v
```

预期全部通过。这些测试只用 DuckDB 内存数据库，不依赖 PostgreSQL。

---

## 第三步：准备 TPC-H 数据 (实验用的生产库)

论文中的 Enterprise-50 实验基于 TPC-H SF=10 (约 10GB) 数据。

### 3.1 编译 TPC-H dbgen

```bash
cd /home/user/mocksql

git clone https://github.com/electrum/tpch-dbgen.git dbgen
cd dbgen
make
cd ..
```

### 3.2 生成数据 (SF=10)

```bash
cd dbgen
./dbgen -s 10 -f
ls -lh *.tbl
# lineitem.tbl 约 7.2GB, orders.tbl 约 1.7GB ...
cd ..
```

> 如果磁盘空间有限，可以用 `-s 1` 生成 SF=1 (约 1GB)，对实验结论影响不大。

### 3.3 建表并加载数据

```bash
export MOCKSQL_DSN="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench"

python scripts/db_setup.py "$MOCKSQL_DSN" --sf=10 --dbgen-path=./dbgen
```

加载过程约 10-30 分钟 (取决于磁盘速度)，脚本会：
1. 创建 8 张 TPC-H 表 (region, nation, supplier, part, partsupp, customer, orders, lineitem)
2. 创建索引
3. 用 COPY 批量导入 `.tbl` 文件
4. 执行 `ANALYZE` 生成统计信息 (这是 MockSQL 的核心依赖)

验证数据：

```bash
psql "$MOCKSQL_DSN" -c "SELECT relname, reltuples::bigint FROM pg_class WHERE relname IN ('lineitem','orders','customer','part','supplier') ORDER BY reltuples DESC;"
```

预期输出 (SF=10)：
```
  relname  | reltuples
-----------+-----------
 lineitem  |  59986052
 orders    |  15000000
 partsupp  |   8000000
 customer  |   1500000
 part      |   2000000
 supplier  |    100000
```

---

## 第四步：下载基准测试数据集

### 4.1 Spider 数据集

```bash
mkdir -p data
cd data

# 方法 A: 使用 gdown (推荐)
pip install gdown
gdown 1iRDVHLr6THdbL2wLzXmGoWJ8F5PrgWkj -O spider.zip
unzip spider.zip
rm spider.zip

# 方法 B: 手动下载
# 访问 https://yale-lily.github.io/spider 下载后解压到 data/spider/

cd ..
```

验证：

```bash
ls data/spider/dev.json data/spider/tables.json
```

### 4.2 BIRD 数据集

```bash
# BIRD 需要从官网手动下载:
# https://bird-bench.github.io/
# 下载 dev set 后解压到 data/bird/
```

验证：

```bash
ls data/bird/dev/dev.json
```

> 如果暂时只想跑 Exp1/3/4/5 (不涉及 LLM 调用)，可以跳过 Spider/BIRD 下载。

---

## 第五步：配置 LLM API Keys

创建 `.env` 文件：

```bash
cat > .env << 'EOF'
# ===== 数据库连接 =====
export MOCKSQL_DSN="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench"

# ===== 数据集路径 =====
export SPIDER_PATH="./data/spider"
export BIRD_PATH="./data/bird"
export TPCH_DBGEN="./dbgen"

# ===== LLM API Keys (按需填写，至少填一个) =====
export OPENAI_API_KEY="sk-xxx"
# export ANTHROPIC_API_KEY="sk-ant-xxx"
# export DEEPSEEK_API_KEY="sk-xxx"
# export QWEN_API_KEY="sk-xxx"
EOF
```

加载环境变量：

```bash
source .env
```

> 实验 2 (Text-to-SQL 准确率) 需要 LLM API Key。实验 1/3/4/5 不需要。

---

## 第六步：运行实验

确保虚拟环境已激活、环境变量已加载：

```bash
source .venv/bin/activate
source .env
```

### 方法 A：一键运行全部实验

```bash
bash scripts/run_all.sh
```

### 方法 B：分步运行单个实验

#### 准备工作：生成测试 SQL

```bash
python -m benchmarks.hazard_generator
# 输出: benchmarks/hazardous_queries.json (200 条)
# 输出: benchmarks/correct_queries.json (800 条)
```

#### 实验 1：安全检出率 (论文 Table 3)

对比 Plan-only、Exec-only、Dual Channel 在 4 类毒 SQL 上的检出率。

```bash
python -m benchmarks.exp1_hazard_detection "$MOCKSQL_DSN"
# 结果: results/exp1_hazard_detection.json
```

#### 实验 2：Text-to-SQL 准确率提升 (论文 Table 4)

需要 LLM API Key 和 Spider/BIRD 数据集。

```bash
# Spider 数据集
python -m benchmarks.exp2_accuracy "$MOCKSQL_DSN" spider ./data/spider

# BIRD 数据集
python -m benchmarks.exp2_accuracy "$MOCKSQL_DSN" bird ./data/bird

# 结果: results/exp2_accuracy.json
```

#### 实验 3：误报率分析 (论文 Table 5)

在 800 条已知正确的 SQL 上测量 MockSQL 的误拒率。

```bash
python -m benchmarks.exp3_false_positives "$MOCKSQL_DSN"
# 结果: results/exp3_false_positives.json
```

#### 实验 4：延迟开销 (论文 Table 6)

测量各组件的延迟分解 (中位数/P95/P99)。

```bash
python -m benchmarks.exp4_latency "$MOCKSQL_DSN"
# 结果: results/exp4_latency.json
```

#### 实验 5：消融实验 (论文 Table 7)

对比 Plan-only / Exec-only / Dual / Dual+Whitelist 的检出率与误报率。

```bash
python -m benchmarks.exp5_ablation "$MOCKSQL_DSN"
# 结果: results/exp5_ablation.json
```

---

## 第七步：生成论文图表

```bash
python -m benchmarks.generate_figures
```

输出到 `paper_figures/` 目录：

| 文件 | 对应论文 |
|------|---------|
| `table_hazard_detection.tex` | Table 3 安全检出率 |
| `table_latency.tex` | Table 6 延迟分解 |
| `fig_detection_rates.pdf` | 检出率分组柱状图 |
| `fig_latency_breakdown.pdf` | 延迟分解柱状图 |

---

## CLI 命令行工具

MockSQL 也可以作为命令行工具单独使用：

```bash
# 检查数据库连接
mocksql --dsn "$MOCKSQL_DSN" check-connection

# 验证单条 SQL
mocksql --dsn "$MOCKSQL_DSN" verify \
  "SELECT * FROM orders JOIN customer ON orders.o_custkey = customer.c_custkey LIMIT 10"

# 验证单条 SQL (JSON 输出)
mocksql --dsn "$MOCKSQL_DSN" verify -j \
  "SELECT * FROM orders, customer"

# 批量验证 (一行一条 SQL)
mocksql --dsn "$MOCKSQL_DSN" batch-verify queries.txt -o results.json
```

---

## Python API 调用

```python
from mocksql import MockSQLProxy, MockSQLConfig
from mocksql.feedback import Verdict

# 初始化
config = MockSQLConfig(prod_dsn="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench")
proxy = MockSQLProxy(config)

# 验证一条 SQL
sql = """
SELECT c.c_name, SUM(o.o_totalprice) as total
FROM customer c
JOIN orders o ON c.c_custkey = o.o_custkey
GROUP BY c.c_name
LIMIT 100
"""

feedback = proxy.verify(sql)

print(f"判定: {feedback.verdict}")       # PASS / WARNING / REJECT
print(f"耗时: {feedback.total_latency_ms:.1f}ms")
print(f"摘要: {feedback.summary}")

# 如果被拒绝，获取给 LLM 的反馈文本
if feedback.verdict == Verdict.REJECT:
    print(feedback.to_llm_feedback())

# 获取 JSON 格式结果
print(feedback.to_json())
```

带 LLM 反馈循环的完整流程：

```python
from mocksql.llm_client import LLMClient

config = MockSQLConfig(
    prod_dsn="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench",
    llm_provider="openai",
    llm_model="gpt-4o",
    llm_api_key="sk-xxx",
)
proxy = MockSQLProxy(config)
llm = LLMClient(config)

# LLM 生成 SQL -> MockSQL 验证 -> 不通过则反馈给 LLM 重写 -> 循环最多 3 次
def revision_callback(original_sql, feedback_text):
    return llm.revise_sql(original_sql, feedback_text)

feedback, results = proxy.verify_and_execute(
    sql="SELECT ...",
    max_retries=3,
    revision_callback=revision_callback,
)
```

---

## 仅运行 Exec Channel (不需要 PostgreSQL)

如果暂时没有 PostgreSQL 环境，可以只启用 Exec Channel 做语义验证：

```python
config = MockSQLConfig(
    prod_dsn="postgresql://dummy",  # 不会真正连接
    enable_plan_channel=False,      # 关闭 Plan Channel
    enable_exec_channel=True,
)
```

此模式下需要手动提供 TableInfo 元数据 (参考 `tests/test_integration.py`)。

---

## 常见问题

### Q: `psycopg2` 安装失败

```bash
# Ubuntu 需要安装 libpq-dev
sudo apt install -y libpq-dev
pip install psycopg2-binary
```

### Q: PostgreSQL 连接被拒绝

```bash
# 检查服务状态
sudo systemctl status postgresql

# 检查 pg_hba.conf 允许密码认证
sudo vim /etc/postgresql/16/main/pg_hba.conf
# 确保有这一行:
# host all all 127.0.0.1/32 md5

sudo systemctl restart postgresql
```

### Q: TPC-H dbgen 编译失败

```bash
# 确保安装了 build-essential
sudo apt install -y build-essential

cd dbgen
make clean
make
```

### Q: 实验 2 运行很慢

实验 2 需要对每条 SQL 调用 LLM API，Spider dev set 有 1034 条。每条需要 1-3 次 API 调用。

```bash
# 可以限制测试数量快速验证流程
python -c "
from benchmarks.exp2_accuracy import run_accuracy_experiment
run_accuracy_experiment('$MOCKSQL_DSN', dataset='spider', dataset_path='./data/spider', max_queries=50)
"
```

### Q: 如何使用自部署的 Qwen / DeepSeek

如果用 vLLM 自部署模型，修改 `.env` 中的 `base_url`：

```bash
export QWEN_API_KEY="any-string"
export QWEN_BASE_URL="http://localhost:8000/v1"
```

代码使用 OpenAI 兼容 API，所有支持 `/v1/chat/completions` 的服务都可以对接。

---

## 实验结果输出示例

### 实验 1 输出

```
================================================================================
Hazard Detection Results (Table 3 in paper)
================================================================================
Config           Cartesian    Full Scan   Empty Join    Agg Error      Overall
--------------------------------------------------------------------------------
plan_only            98.0%        94.0%         0.0%         0.0%        48.0%
exec_only            12.0%         8.0%        88.0%        82.0%        47.5%
dual                 98.0%        94.0%        88.0%        82.0%        90.5%
================================================================================
```

### 实验 4 输出

```
======================================================================
Latency Overhead (Table 6 in paper)
======================================================================
Component                 Median(ms)   P95(ms)   P99(ms)
----------------------------------------------------------------------
Plan Channel                     3.2       5.1       8.7
Exec Channel Total              19.5      26.3      34.8
Data Synthesis                   8.2      10.1      13.5
Sandbox Execution                4.8       7.2       9.8
Feedback Generation              1.8       2.4       3.1
Mocksql Total                   23.5      31.2      42.6
======================================================================
```

---

## 完整快速上手 (复制粘贴即可)

```bash
# ===== 在 Ubuntu 服务器上从零开始 =====

# 1. 系统依赖
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git build-essential wget unzip postgresql-16 postgresql-client-16 libpq-dev
sudo systemctl start postgresql && sudo systemctl enable postgresql

# 2. 创建数据库
sudo -u postgres psql -c "CREATE USER mocksql WITH PASSWORD 'mocksql123';"
sudo -u postgres psql -c "CREATE DATABASE mocksql_bench OWNER mocksql;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE mocksql_bench TO mocksql;"

# 3. 进入项目
cd /path/to/mocksql

# 4. Python 环境
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e ".[dev,bench]"

# 5. TPC-H 数据
git clone https://github.com/electrum/tpch-dbgen.git dbgen && cd dbgen && make && ./dbgen -s 10 -f && cd ..
export MOCKSQL_DSN="postgresql://mocksql:mocksql123@localhost:5432/mocksql_bench"
python scripts/db_setup.py "$MOCKSQL_DSN" --sf=10 --dbgen-path=./dbgen

# 6. 运行测试验证安装
python -m pytest tests/ -v

# 7. 生成测试 SQL 并运行实验
python -m benchmarks.hazard_generator
python -m benchmarks.exp1_hazard_detection "$MOCKSQL_DSN"
python -m benchmarks.exp3_false_positives "$MOCKSQL_DSN"
python -m benchmarks.exp4_latency "$MOCKSQL_DSN"
python -m benchmarks.exp5_ablation "$MOCKSQL_DSN"

# 8. 生成论文图表
python -m benchmarks.generate_figures
```
