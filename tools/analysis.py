"""DuckDB-backed analysis tools exposed to agents as FunctionTool."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import duckdb
import pandas as pd
from autogen_core.tools import FunctionTool

from config import DATA_PROCESSED

MAX_PREVIEW_ROWS = 30
MAX_PREVIEW_CHARS = 4000


@lru_cache(maxsize=1)
def get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    """Return a shared DuckDB connection with tcpflow / flow views registered."""
    con = duckdb.connect()
    tcpflow = DATA_PROCESSED / "tcpflow.parquet"
    flow = DATA_PROCESSED / "flow.parquet"
    if tcpflow.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW tcpflow AS SELECT * FROM '{tcpflow}'"
        )
    if flow.exists():
        con.execute(f"CREATE OR REPLACE VIEW flow AS SELECT * FROM '{flow}'")
    return con


def _df_to_preview(df: pd.DataFrame) -> dict[str, Any]:
    """Return shape + truncated preview for agent consumption."""
    full_rows = len(df)
    preview = df.head(MAX_PREVIEW_ROWS).to_dict(orient="records")
    text = json.dumps(preview, default=str, ensure_ascii=False)
    truncated = len(text) > MAX_PREVIEW_CHARS
    if truncated:
        text = text[:MAX_PREVIEW_CHARS] + "...<truncated>"
    return {
        "rows_returned": full_rows,
        "columns": list(df.columns),
        "preview_rows": min(MAX_PREVIEW_ROWS, full_rows),
        "preview": text,
        "preview_truncated": truncated,
    }


async def run_sql(sql: str) -> str:
    """Run a DuckDB SQL query.

    Available tables:
      - tcpflow(record_time, source_ip, destination_ip, protocol,
                destination_port, uplink_length, downlink_length)
      - flow(record_time, source_ip, source_port, destination_ip,
             destination_port, method, uri, host, user_agent)

    Always LIMIT your query. For large aggregations use GROUP BY + LIMIT.
    Returns JSON with columns, shape, and truncated preview.
    """
    con = get_duckdb_connection()
    try:
        df = con.execute(sql).fetch_df()
    except Exception as e:
        return json.dumps({"error": str(e), "sql": sql}, ensure_ascii=False)
    return json.dumps(_df_to_preview(df), default=str, ensure_ascii=False)


async def list_tables() -> str:
    """List tables/views available in the analysis database with their schemas."""
    con = get_duckdb_connection()
    tables = con.execute("SHOW TABLES").fetch_df()
    out = {}
    for name in tables["name"]:
        schema = con.execute(f"DESCRIBE {name}").fetch_df()
        count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        out[name] = {
            "row_count": int(count),
            "columns": schema[["column_name", "column_type"]].to_dict(
                orient="records"
            ),
        }
    return json.dumps(out, default=str, ensure_ascii=False)


async def profile_column(table: str, column: str, top_k: int = 20) -> str:
    """Profile a column: null rate, unique count, top-K values with counts."""
    con = get_duckdb_connection()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        nulls = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL"
        ).fetchone()[0]
        uniques = con.execute(
            f"SELECT COUNT(DISTINCT {column}) FROM {table}"
        ).fetchone()[0]
        top = (
            con.execute(
                f"""
            SELECT {column} AS value, COUNT(*) AS n
            FROM {table}
            WHERE {column} IS NOT NULL
            GROUP BY {column}
            ORDER BY n DESC
            LIMIT {top_k}
            """
            )
            .fetch_df()
            .to_dict(orient="records")
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(
        {
            "table": table,
            "column": column,
            "total_rows": int(total),
            "null_count": int(nulls),
            "unique_count": int(uniques),
            "top_values": top,
        },
        default=str,
        ensure_ascii=False,
    )


def build_analysis_tools() -> list[FunctionTool]:
    return [
        FunctionTool(
            run_sql,
            description=(
                "Run DuckDB SQL over the analysis database. "
                "Tables: tcpflow, flow. Always LIMIT queries. "
                "Returns columns + truncated preview as JSON."
            ),
            name="run_sql",
        ),
        FunctionTool(
            list_tables,
            description="List available tables with their row counts and columns.",
            name="list_tables",
        ),
        FunctionTool(
            profile_column,
            description=(
                "Profile a column: total/null/unique counts + top-K values. "
                "Args: table (str), column (str), top_k (int, default 20)."
            ),
            name="profile_column",
        ),
    ]
