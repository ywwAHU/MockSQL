"""Feedback Generator: merge findings from Plan and Exec channels into structured feedback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from mocksql.plan_channel import PlanChannelResult, PlanIssue, Severity as PlanSeverity
from mocksql.exec_channel import ExecChannelResult, ExecIssue, ExecSeverity


class Verdict(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    REJECT = "REJECT"


@dataclass
class MockSQLFeedback:
    """Combined feedback from both channels."""
    verdict: Verdict
    plan_issues: list[dict] = field(default_factory=list)
    exec_issues: list[dict] = field(default_factory=list)
    summary: str = ""
    plan_latency_ms: float = 0.0
    exec_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "plan_issues": self.plan_issues,
            "exec_issues": self.exec_issues,
            "summary": self.summary,
            "latency": {
                "plan_ms": round(self.plan_latency_ms, 1),
                "exec_ms": round(self.exec_latency_ms, 1),
                "total_ms": round(self.total_latency_ms, 1),
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_llm_feedback(self) -> str:
        """Generate natural language feedback suitable for LLM prompts."""
        if self.verdict == Verdict.PASS:
            return ""

        lines = []
        lines.append(f"[MockSQL Verification: {self.verdict.value}]")
        lines.append("")

        if self.plan_issues:
            lines.append("Performance Issues (from execution plan analysis):")
            for issue in self.plan_issues:
                sev = issue.get("severity", "WARNING")
                lines.append(f"  [{sev}] {issue['detail']}")
                lines.append(f"  Suggestion: {issue['suggestion']}")
                lines.append("")

        if self.exec_issues:
            lines.append("Semantic Issues (from sandbox execution):")
            for issue in self.exec_issues:
                sev = issue.get("severity", "WARNING")
                lines.append(f"  [{sev}] {issue['detail']}")
                lines.append(f"  Suggestion: {issue['suggestion']}")
                lines.append("")

        lines.append("Please revise your SQL query to address the issues above.")
        return "\n".join(lines)


class FeedbackGenerator:
    """Merge Plan Channel and Exec Channel results into a unified verdict."""

    def generate(
        self,
        plan_result: Optional[PlanChannelResult],
        exec_result: Optional[ExecChannelResult],
    ) -> MockSQLFeedback:
        """Combine both channel results into a single feedback."""
        feedback = MockSQLFeedback(verdict=Verdict.PASS)

        has_critical = False
        has_warning = False

        # Process Plan Channel issues
        if plan_result:
            feedback.plan_latency_ms = plan_result.latency_ms
            for issue in plan_result.issues:
                issue_dict = {
                    "severity": issue.severity.value,
                    "type": issue.risk_type.value,
                    "detail": issue.detail,
                    "suggestion": issue.suggestion,
                }
                if issue.table_name:
                    issue_dict["table"] = issue.table_name
                if issue.estimated_rows:
                    issue_dict["estimated_rows"] = issue.estimated_rows
                if issue.estimated_cost:
                    issue_dict["estimated_cost"] = issue.estimated_cost

                feedback.plan_issues.append(issue_dict)

                if issue.severity == PlanSeverity.CRITICAL:
                    has_critical = True
                elif issue.severity == PlanSeverity.WARNING:
                    has_warning = True

        # Process Exec Channel issues
        if exec_result:
            feedback.exec_latency_ms = exec_result.latency_ms
            for issue in exec_result.issues:
                issue_dict = {
                    "severity": issue.severity.value,
                    "type": issue.issue_type.value,
                    "detail": issue.detail,
                    "suggestion": issue.suggestion,
                }
                if issue.column_name:
                    issue_dict["column"] = issue.column_name

                feedback.exec_issues.append(issue_dict)

                if issue.severity == ExecSeverity.CRITICAL:
                    has_critical = True
                elif issue.severity == ExecSeverity.WARNING:
                    has_warning = True

        # Determine verdict
        if has_critical:
            feedback.verdict = Verdict.REJECT
        elif has_warning:
            feedback.verdict = Verdict.WARNING
        else:
            feedback.verdict = Verdict.PASS

        # Calculate total latency (channels run concurrently)
        feedback.total_latency_ms = max(
            feedback.plan_latency_ms, feedback.exec_latency_ms
        )

        # Generate summary
        feedback.summary = self._generate_summary(feedback)

        return feedback

    def _generate_summary(self, feedback: MockSQLFeedback) -> str:
        """Generate a concise natural language summary."""
        if feedback.verdict == Verdict.PASS:
            return "Query passed all verification checks."

        parts = []
        n_plan = len(feedback.plan_issues)
        n_exec = len(feedback.exec_issues)

        if n_plan > 0:
            critical_plan = sum(
                1 for i in feedback.plan_issues if i["severity"] == "CRITICAL"
            )
            if critical_plan > 0:
                parts.append(
                    f"{critical_plan} critical performance issue(s) detected"
                )
            else:
                parts.append(f"{n_plan} performance warning(s) detected")

        if n_exec > 0:
            critical_exec = sum(
                1 for i in feedback.exec_issues if i["severity"] == "CRITICAL"
            )
            if critical_exec > 0:
                parts.append(
                    f"{critical_exec} critical semantic issue(s) detected"
                )
            else:
                parts.append(f"{n_exec} semantic warning(s) detected")

        return "; ".join(parts) + "."
