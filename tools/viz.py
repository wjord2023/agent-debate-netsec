"""Plotting + graph tools for agents."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # non-interactive
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from autogen_core.tools import FunctionTool  # noqa: E402

from config import OUTPUTS  # noqa: E402
from tools.analysis import get_duckdb_connection  # noqa: E402

_SAFE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name)[:80] or "chart"


def _save(fig: plt.Figure, name: str) -> Path:
    path = OUTPUTS / f"{_safe_name(name)}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


async def plot_bar(sql: str, label_col: str, value_col: str, title: str) -> str:
    """Run a SQL and render a horizontal bar chart.

    SQL must return rows with at least `label_col` and `value_col`.
    Saved to outputs/<title>.png. Returns {path, rows}.
    """
    con = get_duckdb_connection()
    try:
        df = con.execute(sql).fetch_df()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if df.empty:
        return json.dumps({"error": "empty result"}, ensure_ascii=False)
    df = df.head(30).iloc[::-1]  # descending → reverse for horizontal display

    fig, ax = plt.subplots(figsize=(9, max(3, 0.3 * len(df))))
    ax.barh(df[label_col].astype(str), df[value_col])
    ax.set_xlabel(value_col)
    ax.set_title(title)
    path = _save(fig, title)
    return json.dumps(
        {"path": str(path), "rows": len(df)}, ensure_ascii=False
    )


async def plot_time_series(
    sql: str, time_col: str, value_col: str, title: str
) -> str:
    """Run a SQL and render a line chart over time.

    SQL must return rows with `time_col` and `value_col`.
    """
    con = get_duckdb_connection()
    try:
        df = con.execute(sql).fetch_df()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if df.empty:
        return json.dumps({"error": "empty result"}, ensure_ascii=False)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df[time_col], df[value_col])
    ax.set_xlabel(time_col)
    ax.set_ylabel(value_col)
    ax.set_title(title)
    fig.autofmt_xdate()
    path = _save(fig, title)
    return json.dumps(
        {"path": str(path), "rows": len(df)}, ensure_ascii=False
    )


async def build_comm_graph(
    sql: str, src_col: str, dst_col: str, weight_col: str, title: str
) -> str:
    """Build a communication graph from SQL result and save PNG + metrics.

    SQL must return rows with src, dst, weight. Keeps top 100 edges by weight.
    """
    con = get_duckdb_connection()
    try:
        df = con.execute(sql).fetch_df()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if df.empty:
        return json.dumps({"error": "empty result"}, ensure_ascii=False)
    df = df.nlargest(100, weight_col)

    G = nx.DiGraph()
    for _, r in df.iterrows():
        G.add_edge(str(r[src_col]), str(r[dst_col]), weight=float(r[weight_col]))

    fig, ax = plt.subplots(figsize=(11, 9))
    pos = nx.spring_layout(G, seed=42, k=0.7)
    deg = dict(G.degree())
    sizes = [200 + 20 * deg[n] for n in G.nodes()]
    nx.draw_networkx_nodes(G, pos, node_size=sizes, alpha=0.8, ax=ax)
    nx.draw_networkx_edges(G, pos, alpha=0.3, arrows=True, ax=ax)
    nx.draw_networkx_labels(G, pos, font_size=7, ax=ax)
    ax.set_title(title)
    ax.axis("off")
    path = _save(fig, title)

    metrics: dict[str, Any] = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "top_degree": sorted(deg.items(), key=lambda x: -x[1])[:10],
    }
    return json.dumps(
        {"path": str(path), "metrics": metrics},
        default=str,
        ensure_ascii=False,
    )


def build_viz_tools() -> list[FunctionTool]:
    return [
        FunctionTool(
            plot_bar,
            description=(
                "Render a horizontal bar chart from a SQL result. "
                "Args: sql, label_col, value_col, title."
            ),
            name="plot_bar",
        ),
        FunctionTool(
            plot_time_series,
            description=(
                "Render a line chart over time. Args: sql, time_col, value_col, title."
            ),
            name="plot_time_series",
        ),
        FunctionTool(
            build_comm_graph,
            description=(
                "Build a communication graph (top 100 edges). "
                "Args: sql, src_col, dst_col, weight_col, title."
            ),
            name="build_comm_graph",
        ),
    ]
