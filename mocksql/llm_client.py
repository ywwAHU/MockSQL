"""LLM integration: call various LLM APIs for Text-to-SQL and revision."""

from __future__ import annotations

import logging
from typing import Optional

from mocksql.config import MockSQLConfig

logger = logging.getLogger(__name__)

# System prompt for Text-to-SQL
TEXT_TO_SQL_SYSTEM = """You are an expert SQL developer. Given a database schema and a natural language question, generate a correct SQL query.

Rules:
- Output ONLY the SQL query, no explanations.
- Use standard SQL syntax compatible with PostgreSQL.
- Always use appropriate JOIN conditions.
- Do not use SELECT * unless explicitly asked.
- Use table aliases for readability.
"""

REVISION_SYSTEM = """You are an expert SQL developer. The previous SQL query you generated had issues detected by a verification system. Please revise the query based on the feedback provided.

Rules:
- Output ONLY the revised SQL query, no explanations.
- Address ALL issues mentioned in the feedback.
- Maintain the original query intent.
"""


def build_schema_prompt(tables_info: dict) -> str:
    """Build a schema description for LLM prompt."""
    lines = ["Database Schema:"]
    for tname, info in tables_info.items():
        cols = []
        for col in info.columns:
            parts = [f"{col.name} {col.data_type}"]
            if col.is_primary_key:
                parts.append("PRIMARY KEY")
            if col.foreign_key:
                parts.append(f"REFERENCES {col.foreign_key[0]}({col.foreign_key[1]})")
            if not col.is_nullable:
                parts.append("NOT NULL")
            cols.append("  " + " ".join(parts))
        lines.append(f"\nTable: {tname}")
        lines.append("\n".join(cols))
    return "\n".join(lines)


class LLMClient:
    """Unified interface for calling different LLM providers."""

    def __init__(self, config: MockSQLConfig):
        self._config = config
        self._provider = config.llm_provider
        self._model = config.llm_model
        self._api_key = config.llm_api_key
        self._base_url = config.llm_base_url

    def generate_sql(self, question: str, schema_prompt: str) -> str:
        """Generate SQL from a natural language question."""
        user_msg = f"{schema_prompt}\n\nQuestion: {question}\n\nSQL:"
        return self._call(TEXT_TO_SQL_SYSTEM, user_msg)

    def revise_sql(self, original_sql: str, feedback: str) -> str:
        """Revise SQL based on MockSQL feedback."""
        user_msg = (
            f"Original SQL:\n{original_sql}\n\n"
            f"Verification Feedback:\n{feedback}\n\n"
            f"Revised SQL:"
        )
        return self._call(REVISION_SYSTEM, user_msg)

    def _call(self, system: str, user: str) -> str:
        """Call the LLM API."""
        if self._provider == "openai" or self._provider == "deepseek" or self._provider == "qwen":
            return self._call_openai_compatible(system, user)
        elif self._provider == "anthropic":
            return self._call_anthropic(system, user)
        else:
            raise ValueError(f"Unknown LLM provider: {self._provider}")

    def _call_openai_compatible(self, system: str, user: str) -> str:
        """Call OpenAI-compatible APIs (OpenAI, DeepSeek, Qwen via vLLM, etc.)."""
        from openai import OpenAI

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=2048,
        )

        content = response.choices[0].message.content.strip()
        return self._extract_sql(content)

    def _call_anthropic(self, system: str, user: str) -> str:
        """Call Anthropic API."""
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)

        response = client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        content = response.content[0].text.strip()
        return self._extract_sql(content)

    def _extract_sql(self, text: str) -> str:
        """Extract SQL from LLM response, handling markdown code blocks."""
        text = text.strip()
        # Remove markdown code blocks
        if text.startswith("```sql"):
            text = text[6:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
