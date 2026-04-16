"""MockSQL CLI: command-line interface for verification and benchmarking."""

import json
import logging
import sys

import click

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.feedback import Verdict


@click.group()
@click.option("--dsn", envvar="MOCKSQL_DSN", default="postgresql://localhost:5432/production",
              help="Production database DSN")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def main(ctx, dsn, verbose):
    """MockSQL: Dual-Channel Verification Middleware for Safe LLM-Database Interactions."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["dsn"] = dsn


@main.command()
@click.argument("sql")
@click.option("--plan-only", is_flag=True, help="Use Plan Channel only")
@click.option("--exec-only", is_flag=True, help="Use Exec Channel only")
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON")
@click.pass_context
def verify(ctx, sql, plan_only, exec_only, json_output):
    """Verify a SQL query without executing it."""
    config = MockSQLConfig(prod_dsn=ctx.obj["dsn"])
    if plan_only:
        config.enable_exec_channel = False
    if exec_only:
        config.enable_plan_channel = False

    proxy = MockSQLProxy(config)
    feedback = proxy.verify(sql)

    if json_output:
        click.echo(feedback.to_json())
    else:
        _print_feedback(feedback)


@main.command()
@click.argument("sql_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Output results to file")
@click.pass_context
def batch_verify(ctx, sql_file, output):
    """Verify multiple SQL queries from a file (one per line)."""
    config = MockSQLConfig(prod_dsn=ctx.obj["dsn"])
    proxy = MockSQLProxy(config)

    with open(sql_file) as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    results = []
    for i, sql in enumerate(queries, 1):
        click.echo(f"[{i}/{len(queries)}] Verifying...", nl=False)
        feedback = proxy.verify(sql)
        results.append({
            "query_id": i,
            "sql": sql,
            "verdict": feedback.verdict.value,
            "issues": len(feedback.plan_issues) + len(feedback.exec_issues),
        })
        click.echo(f" {feedback.verdict.value}")

    if output:
        with open(output, "w") as f:
            json.dump(results, f, indent=2)
        click.echo(f"\nResults written to {output}")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    warned = sum(1 for r in results if r["verdict"] == "WARNING")
    rejected = sum(1 for r in results if r["verdict"] == "REJECT")
    click.echo(f"\nSummary: {passed} PASS, {warned} WARNING, {rejected} REJECT (total: {total})")


@main.command()
@click.pass_context
def check_connection(ctx):
    """Check connectivity to the production database."""
    import psycopg2
    dsn = ctx.obj["dsn"]
    click.echo(f"Connecting to {dsn}...")
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        conn.close()
        click.echo(f"Connected: {version}")
    except Exception as e:
        click.echo(f"Connection failed: {e}", err=True)
        sys.exit(1)


def _print_feedback(feedback):
    """Pretty-print feedback to terminal."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        color = {"PASS": "green", "WARNING": "yellow", "REJECT": "red"}[feedback.verdict.value]
        console.print(f"\n[bold {color}]Verdict: {feedback.verdict.value}[/]")
        console.print(f"Summary: {feedback.summary}")
        console.print(
            f"Latency: plan={feedback.plan_latency_ms:.1f}ms, "
            f"exec={feedback.exec_latency_ms:.1f}ms, "
            f"total={feedback.total_latency_ms:.1f}ms"
        )

        if feedback.plan_issues:
            table = Table(title="Plan Channel Issues")
            table.add_column("Severity", style="bold")
            table.add_column("Type")
            table.add_column("Detail")
            table.add_column("Suggestion")
            for issue in feedback.plan_issues:
                sev_color = "red" if issue["severity"] == "CRITICAL" else "yellow"
                table.add_row(
                    f"[{sev_color}]{issue['severity']}[/]",
                    issue.get("type", ""),
                    issue["detail"],
                    issue["suggestion"],
                )
            console.print(table)

        if feedback.exec_issues:
            table = Table(title="Exec Channel Issues")
            table.add_column("Severity", style="bold")
            table.add_column("Type")
            table.add_column("Detail")
            table.add_column("Suggestion")
            for issue in feedback.exec_issues:
                sev_color = "red" if issue["severity"] == "CRITICAL" else "yellow"
                table.add_row(
                    f"[{sev_color}]{issue['severity']}[/]",
                    issue.get("type", ""),
                    issue["detail"],
                    issue["suggestion"],
                )
            console.print(table)

    except ImportError:
        # Fallback without rich
        print(f"\nVerdict: {feedback.verdict.value}")
        print(f"Summary: {feedback.summary}")
        for issue in feedback.plan_issues + feedback.exec_issues:
            print(f"  [{issue['severity']}] {issue['detail']}")
            print(f"    -> {issue['suggestion']}")


if __name__ == "__main__":
    main()
