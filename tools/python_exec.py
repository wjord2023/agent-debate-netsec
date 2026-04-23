"""In-process Python REPL exposed to agents as a FunctionTool.

Pre-loaded names (no need for agent to import):
    con:       DuckDB connection with tcpflow / flow views
    pd, np:    pandas, numpy
    plt:       matplotlib.pyplot (Agg backend)
    sns:       seaborn
    nx:        networkx
    re, json, ipaddress, urllib:  std-lib
    OUTPUTS:   Path — save generated artifacts here
    KMeans, DBSCAN, StandardScaler:   from scikit-learn
    user_agents:  UA parser package
    tldextract:   domain extractor

The last expression of the snippet is auto-`repr`-ed to stdout (like Jupyter).
Stdout is captured and returned (truncated to 6000 chars).
Execution is in-process; state does NOT persist between calls.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import traceback
from typing import Any

from autogen_core.tools import FunctionTool

from config import OUTPUTS
from tools.analysis import get_duckdb_connection

MAX_STDOUT_CHARS = 6000


def _build_namespace() -> dict[str, Any]:
    import ipaddress
    import json as _json
    import re
    import urllib.parse
    from pathlib import Path

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from sklearn.cluster import DBSCAN, KMeans
    from sklearn.preprocessing import StandardScaler

    ns: dict[str, Any] = {
        "con": get_duckdb_connection(),
        "pd": pd,
        "np": np,
        "plt": plt,
        "sns": sns,
        "nx": nx,
        "re": re,
        "json": _json,
        "ipaddress": ipaddress,
        "urllib": urllib,
        "Path": Path,
        "OUTPUTS": OUTPUTS,
        "KMeans": KMeans,
        "DBSCAN": DBSCAN,
        "StandardScaler": StandardScaler,
    }
    try:
        import user_agents
        ns["user_agents"] = user_agents
    except ImportError:
        pass
    try:
        import tldextract
        ns["tldextract"] = tldextract
    except ImportError:
        pass
    return ns


async def run_python(code: str) -> str:
    """Execute a Python snippet in-process.

    Names already available: con (DuckDB), pd, np, plt, sns, nx,
    re, json, ipaddress, urllib, Path, OUTPUTS, KMeans, DBSCAN,
    StandardScaler, user_agents, tldextract.
    Do NOT re-import them.

    Use `con.execute(sql).fetch_df()` for DuckDB queries returning DataFrames.
    Save artifacts under `OUTPUTS / "<name>.png"`.

    Last expression is auto-printed. State does not persist across calls.
    """
    ns = _build_namespace()
    stdout = io.StringIO()
    files_before = set(OUTPUTS.iterdir()) if OUTPUTS.exists() else set()
    result = {"ok": True, "stdout": "", "error": None, "new_files": []}

    try:
        parsed = ast.parse(code)
    except SyntaxError as e:
        return json.dumps(
            {"ok": False, "error": f"SyntaxError: {e}", "stdout": "", "new_files": []},
            ensure_ascii=False,
        )

    with contextlib.redirect_stdout(stdout):
        try:
            if parsed.body and isinstance(parsed.body[-1], ast.Expr):
                last_expr = parsed.body.pop()
                if parsed.body:
                    exec(
                        compile(
                            ast.Module(body=parsed.body, type_ignores=[]),
                            "<agent>",
                            "exec",
                        ),
                        ns,
                    )
                value = eval(
                    compile(ast.Expression(body=last_expr.value), "<agent>", "eval"),
                    ns,
                )
                if value is not None:
                    try:
                        print(repr(value))
                    except Exception:
                        print(f"<unprintable: {type(value).__name__}>")
            else:
                exec(compile(parsed, "<agent>", "exec"), ns)
        except Exception:
            result["ok"] = False
            result["error"] = traceback.format_exc(limit=6)

    out = stdout.getvalue()
    if len(out) > MAX_STDOUT_CHARS:
        out = out[:MAX_STDOUT_CHARS] + "\n...<truncated>"
    result["stdout"] = out

    files_after = set(OUTPUTS.iterdir()) if OUTPUTS.exists() else set()
    new_files = sorted(f.name for f in files_after - files_before)
    result["new_files"] = new_files

    return json.dumps(result, default=str, ensure_ascii=False)


def build_python_tool() -> FunctionTool:
    return FunctionTool(
        run_python,
        description=(
            "Run a Python snippet in-process. Pre-loaded: con (DuckDB with "
            "tcpflow/flow views), pd, np, plt, sns, nx, re, json, ipaddress, "
            "urllib, Path, OUTPUTS (Path), KMeans, DBSCAN, StandardScaler, "
            "user_agents, tldextract. Do NOT re-import. "
            "Last expression is auto-printed. State does not persist. "
            "Use this when SQL alone is not enough (e.g. clustering, regex "
            "parsing, custom plots, pandas merges)."
        ),
        name="run_python",
    )
