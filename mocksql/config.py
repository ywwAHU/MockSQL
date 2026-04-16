"""Configuration for MockSQL."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MockSQLConfig:
    """MockSQL runtime configuration."""

    # --- Production DB connection ---
    prod_dsn: str = "postgresql://localhost:5432/production"

    # --- Plan Channel thresholds ---
    plan_row_explosion_threshold: int = 1_000_000
    plan_full_scan_row_threshold: int = 100_000
    plan_total_cost_threshold: float = 10_000.0

    # --- Exec Channel ---
    sandbox_engine: str = "duckdb"  # "duckdb" or "sqlite"
    default_rows_per_table: int = 100
    ndv_multiplier: int = 10  # target rows before capping by max_sandbox_rows_per_table
    max_sandbox_rows_per_table: int = 100

    # --- Metadata cache ---
    metadata_cache_ttl_seconds: int = 300  # 5 minutes

    # --- Retry ---
    max_retries: int = 3

    # --- Feedback ---
    feedback_language: str = "en"  # "en" or "zh"

    # --- Channels ---
    enable_plan_channel: bool = True
    enable_exec_channel: bool = True

    # --- Whitelist ---
    whitelist_patterns: list[str] = field(default_factory=list)

    # --- LLM config (for benchmark) ---
    llm_provider: Optional[str] = None  # "openai", "anthropic", "deepseek", "qwen"
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
