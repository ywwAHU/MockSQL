"""Tests for feedback generation."""

import pytest

from mocksql.plan_channel import PlanChannelResult, PlanIssue, Severity, RiskType
from mocksql.exec_channel import ExecChannelResult, ExecIssue, ExecSeverity, ExecIssueType
from mocksql.feedback import FeedbackGenerator, Verdict


@pytest.fixture
def gen():
    return FeedbackGenerator()


class TestFeedbackGenerator:
    def test_pass_when_no_issues(self, gen):
        plan = PlanChannelResult()
        exec_ = ExecChannelResult()
        fb = gen.generate(plan, exec_)
        assert fb.verdict == Verdict.PASS
        assert len(fb.plan_issues) == 0
        assert len(fb.exec_issues) == 0

    def test_reject_on_critical_plan(self, gen):
        plan = PlanChannelResult(issues=[
            PlanIssue(
                severity=Severity.CRITICAL,
                risk_type=RiskType.CARTESIAN_PRODUCT,
                detail="Cartesian product detected",
                suggestion="Add JOIN condition",
            )
        ])
        exec_ = ExecChannelResult()
        fb = gen.generate(plan, exec_)
        assert fb.verdict == Verdict.REJECT
        assert len(fb.plan_issues) == 1

    def test_warning_on_warning_only(self, gen):
        plan = PlanChannelResult(issues=[
            PlanIssue(
                severity=Severity.WARNING,
                risk_type=RiskType.FULL_TABLE_SCAN,
                detail="Full scan on large table",
                suggestion="Add WHERE clause",
            )
        ])
        exec_ = ExecChannelResult()
        fb = gen.generate(plan, exec_)
        assert fb.verdict == Verdict.WARNING

    def test_reject_on_critical_exec(self, gen):
        plan = PlanChannelResult()
        exec_ = ExecChannelResult(issues=[
            ExecIssue(
                severity=ExecSeverity.CRITICAL,
                issue_type=ExecIssueType.EXECUTION_ERROR,
                detail="SQL execution failed",
                suggestion="Fix syntax",
            )
        ])
        fb = gen.generate(plan, exec_)
        assert fb.verdict == Verdict.REJECT

    def test_dual_channel_issues(self, gen):
        plan = PlanChannelResult(issues=[
            PlanIssue(
                severity=Severity.CRITICAL,
                risk_type=RiskType.ROW_EXPLOSION,
                detail="1B rows estimated",
                suggestion="Add filter",
            )
        ])
        exec_ = ExecChannelResult(issues=[
            ExecIssue(
                severity=ExecSeverity.WARNING,
                issue_type=ExecIssueType.EMPTY_RESULT,
                detail="Empty result",
                suggestion="Check JOINs",
            )
        ])
        fb = gen.generate(plan, exec_)
        assert fb.verdict == Verdict.REJECT
        assert len(fb.plan_issues) == 1
        assert len(fb.exec_issues) == 1

    def test_llm_feedback_format(self, gen):
        plan = PlanChannelResult(issues=[
            PlanIssue(
                severity=Severity.CRITICAL,
                risk_type=RiskType.CARTESIAN_PRODUCT,
                detail="Cartesian product detected",
                suggestion="Add JOIN condition",
            )
        ])
        fb = gen.generate(plan, None)
        llm_text = fb.to_llm_feedback()
        assert "REJECT" in llm_text
        assert "Cartesian product" in llm_text
        assert "Please revise" in llm_text

    def test_pass_produces_empty_feedback(self, gen):
        fb = gen.generate(PlanChannelResult(), ExecChannelResult())
        assert fb.to_llm_feedback() == ""

    def test_json_output(self, gen):
        plan = PlanChannelResult(latency_ms=3.2)
        exec_ = ExecChannelResult(latency_ms=19.5)
        fb = gen.generate(plan, exec_)
        d = fb.to_dict()
        assert d["verdict"] == "PASS"
        assert "latency" in d
        assert d["latency"]["plan_ms"] == 3.2
