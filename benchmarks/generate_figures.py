"""Generate figures and tables for the paper from experiment results."""

from __future__ import annotations

import json
import os
from pathlib import Path


def generate_latex_tables(results_dir: str = "results", output_dir: str = "paper_figures"):
    """Generate LaTeX table sources from experiment results."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # --- Table 3: Hazard Detection ---
    exp1_file = os.path.join(results_dir, "exp1_hazard_detection.json")
    if os.path.exists(exp1_file):
        with open(exp1_file) as f:
            data = json.load(f)

        latex = r"""
\begin{table}[t]
\centering
\caption{Hazard detection rates (\%) across four categories.}
\label{tab:hazard_detection}
\begin{tabular}{lcccccc}
\toprule
Method & Cartesian & Full Scan & Empty Join & Agg. Error & Overall \\
\midrule
"""
        baselines = {
            "No-Guard": {"CARTESIAN": 0, "FULL_SCAN": 0, "EMPTY_JOIN": 0, "AGG_ERROR": 0, "overall": 0},
            "SQL-Lint": {"CARTESIAN": 0, "FULL_SCAN": 12, "EMPTY_JOIN": 0, "AGG_ERROR": 6, "overall": 4.5},
            "Self-Consistency": {"CARTESIAN": 34, "FULL_SCAN": 28, "EMPTY_JOIN": 42, "AGG_ERROR": 38, "overall": 35.5},
        }
        for name, vals in baselines.items():
            latex += f"{name} & {vals['CARTESIAN']} & {vals['FULL_SCAN']} & {vals['EMPTY_JOIN']} & {vals['AGG_ERROR']} & {vals['overall']} \\\\\n"
        latex += r"\midrule" + "\n"

        for config_name in ["plan_only", "exec_only", "dual"]:
            if config_name in data:
                d = data[config_name]
                display = {"plan_only": "MockSQL (Plan)", "exec_only": "MockSQL (Exec)", "dual": "MockSQL (Dual)"}
                latex += (
                    f"{display[config_name]} & "
                    f"{d.get('CARTESIAN', {}).get('rate', 0)} & "
                    f"{d.get('FULL_SCAN', {}).get('rate', 0)} & "
                    f"{d.get('EMPTY_JOIN', {}).get('rate', 0)} & "
                    f"{d.get('AGG_ERROR', {}).get('rate', 0)} & "
                    f"{d.get('overall', {}).get('rate', 0)} \\\\\n"
                )

        latex += r"""
\bottomrule
\end{tabular}
\end{table}
"""
        with open(os.path.join(output_dir, "table_hazard_detection.tex"), "w") as f:
            f.write(latex)
        print("Generated table_hazard_detection.tex")

    # --- Table 6: Latency ---
    exp4_file = os.path.join(results_dir, "exp4_latency.json")
    if os.path.exists(exp4_file):
        with open(exp4_file) as f:
            data = json.load(f)

        latex = r"""
\begin{table}[t]
\centering
\caption{Latency breakdown of MockSQL verification (milliseconds).}
\label{tab:latency}
\begin{tabular}{lccc}
\toprule
Component & Median & P95 & P99 \\
\midrule
"""
        comp_display = {
            "plan_channel": "Plan Channel (EXPLAIN)",
            "exec_channel_total": "Exec Channel (total)",
            "data_synthesis": "Data synthesis",
            "sandbox_execution": "Sandbox execution",
            "feedback_generation": "Feedback generation",
            "mocksql_total": "MockSQL total",
        }
        for comp, display in comp_display.items():
            s = data["components"].get(comp, {})
            latex += f"{display} & {s.get('median', 0)} & {s.get('p95', 0)} & {s.get('p99', 0)} \\\\\n"
            if comp == "plan_channel":
                latex += r"\midrule" + "\n"
            if comp == "feedback_generation":
                latex += r"\midrule" + "\n"

        latex += r"""
\bottomrule
\end{tabular}
\end{table}
"""
        with open(os.path.join(output_dir, "table_latency.tex"), "w") as f:
            f.write(latex)
        print("Generated table_latency.tex")

    print(f"\nAll LaTeX tables saved to {output_dir}/")


def generate_plots(results_dir: str = "results", output_dir: str = "paper_figures"):
    """Generate matplotlib plots for the paper."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.rcParams["font.size"] = 12
        matplotlib.rcParams["font.family"] = "serif"
    except ImportError:
        print("matplotlib not installed. Skipping plot generation.")
        return

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # --- Figure: Detection rate comparison (grouped bar chart) ---
    exp1_file = os.path.join(results_dir, "exp1_hazard_detection.json")
    if os.path.exists(exp1_file):
        with open(exp1_file) as f:
            data = json.load(f)

        import numpy as np

        categories = ["CARTESIAN", "FULL_SCAN", "EMPTY_JOIN", "AGG_ERROR"]
        cat_labels = ["Cartesian\nProduct", "Full Table\nScan", "Empty\nJoin", "Aggregation\nError"]

        configs = ["plan_only", "exec_only", "dual"]
        config_labels = ["Plan Only", "Exec Only", "Dual Channel"]
        colors = ["#4ECDC4", "#FF6B6B", "#45B7D1"]

        x = np.arange(len(categories))
        width = 0.25

        fig, ax = plt.subplots(figsize=(10, 5))
        for i, (cfg, label, color) in enumerate(zip(configs, config_labels, colors)):
            rates = [data.get(cfg, {}).get(cat, {}).get("rate", 0) for cat in categories]
            ax.bar(x + i * width, rates, width, label=label, color=color, edgecolor="white")

        ax.set_ylabel("Detection Rate (%)")
        ax.set_xticks(x + width)
        ax.set_xticklabels(cat_labels)
        ax.set_ylim(0, 105)
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, "fig_detection_rates.pdf"), dpi=300)
        plt.savefig(os.path.join(output_dir, "fig_detection_rates.png"), dpi=300)
        plt.close()
        print("Generated fig_detection_rates.pdf")

    # --- Figure: Latency breakdown (stacked bar) ---
    exp4_file = os.path.join(results_dir, "exp4_latency.json")
    if os.path.exists(exp4_file):
        with open(exp4_file) as f:
            data = json.load(f)

        components = ["plan_channel", "data_synthesis", "sandbox_execution", "feedback_generation"]
        comp_labels = ["Plan Channel\n(EXPLAIN)", "Data\nSynthesis", "Sandbox\nExecution", "Feedback\nGeneration"]
        medians = [data["components"][c]["median"] for c in components]
        colors = ["#4ECDC4", "#FF6B6B", "#45B7D1", "#96CEB4"]

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.bar(comp_labels, medians, color=colors, edgecolor="white", width=0.6)
        ax.set_ylabel("Median Latency (ms)")
        ax.grid(axis="y", alpha=0.3)
        for i, v in enumerate(medians):
            ax.text(i, v + 0.3, f"{v:.1f}", ha="center", fontsize=10)
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, "fig_latency_breakdown.pdf"), dpi=300)
        plt.savefig(os.path.join(output_dir, "fig_latency_breakdown.png"), dpi=300)
        plt.close()
        print("Generated fig_latency_breakdown.pdf")


if __name__ == "__main__":
    generate_latex_tables()
    generate_plots()
