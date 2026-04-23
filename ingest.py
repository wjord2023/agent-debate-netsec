"""One-time conversion: raw logs → parquet.

tcpflow: 5 gzipped newline-delimited JSON files → tcpflow.parquet
flow   : 2 JSON array files → normalize to NDJSON first → flow.parquet
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

import duckdb

from config import DATA_PROCESSED, DATA_RAW_FLOW_DIR, DATA_RAW_TCPFLOW


def ingest_tcpflow(force: bool = False) -> Path:
    out = DATA_PROCESSED / "tcpflow.parquet"
    if out.exists() and not force:
        print(f"[skip] {out} already exists (use --force to rebuild)")
        return out

    pattern = str(DATA_RAW_TCPFLOW / "part-*.gz")
    files = sorted(DATA_RAW_TCPFLOW.glob("part-*.gz"))
    if not files:
        raise FileNotFoundError(
            f"No tcpflow files under {DATA_RAW_TCPFLOW}. "
            f"Did you unzip dataset_1.zip?"
        )
    print(f"[tcpflow] reading {len(files)} gz files")
    t0 = time.time()

    tmp = out.with_suffix(".parquet.tmp")
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT
                CAST(record_time AS TIMESTAMP) AS record_time,
                source_ip,
                destination_ip,
                protocol,
                TRY_CAST(destination_port AS INTEGER) AS destination_port,
                TRY_CAST(uplink_length AS BIGINT) AS uplink_length,
                TRY_CAST(downlink_length AS BIGINT) AS downlink_length
            FROM read_json('{pattern}',
                format = 'newline_delimited',
                ignore_errors = true,
                compression = 'gzip')
        ) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """
    )
    tmp.replace(out)
    n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    print(f"[tcpflow] wrote {n:,} rows → {out}  ({time.time()-t0:.1f}s)")
    return out


def _flow_array_to_ndjson(src: Path, dst_gz: Path) -> tuple[int, int]:
    """Stream a `[{...}\\t\\n,{...}]` file into gzipped NDJSON.

    Records are separated by `}` followed by `,{` with optional whitespace.
    Returns (ok_count, bad_count).
    """
    ok = bad = 0
    # We split on `},{` (ignoring whitespace between `}` and `,`, and between
    # `,` and `{`). Simplest: read whole file (flow files are 100-400MB), then
    # parse with json if it's a well-formed array; else use a token scanner.
    text = src.read_text(encoding="utf-8", errors="replace")
    text = text.strip()
    # Try fast-path: standard JSON array
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        arr = None

    with gzip.open(dst_gz, "wt", encoding="utf-8") as out:
        if arr is not None:
            for rec in arr:
                out.write(json.dumps(rec, ensure_ascii=False))
                out.write("\n")
                ok += 1
            return ok, 0

        # Slow-path: brace-aware splitter (handles `}\t\n,{` and friends)
        # Strip outer `[ ... ]`
        if text.startswith("["):
            text = text[1:]
        if text.endswith("]"):
            text = text[:-1]

        depth = 0
        in_str = False
        esc = False
        start = None
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and start is not None:
                        chunk = text[start : i + 1]
                        try:
                            rec = json.loads(chunk)
                            out.write(json.dumps(rec, ensure_ascii=False))
                            out.write("\n")
                            ok += 1
                        except json.JSONDecodeError:
                            bad += 1
                        start = None
            i += 1
    return ok, bad


def ingest_flow(force: bool = False) -> Path:
    out = DATA_PROCESSED / "flow.parquet"
    if out.exists() and not force:
        print(f"[skip] {out} already exists (use --force to rebuild)")
        return out

    files = sorted(DATA_RAW_FLOW_DIR.glob("part-*.json"))
    if not files:
        raise FileNotFoundError(f"No flow files under {DATA_RAW_FLOW_DIR}")
    print(f"[flow] normalizing {len(files)} json files → NDJSON")
    t0 = time.time()

    ndjson_dir = DATA_PROCESSED / "_flow_ndjson"
    ndjson_dir.mkdir(exist_ok=True)
    total_ok = total_bad = 0
    for src in files:
        dst = ndjson_dir / (src.stem + ".ndjson.gz")
        if dst.exists() and not force:
            print(f"  [skip] {dst.name} already normalized")
            continue
        ok, bad = _flow_array_to_ndjson(src, dst)
        total_ok += ok
        total_bad += bad
        print(f"  {src.name} → {dst.name}  ok={ok:,} bad={bad}")
    print(f"[flow] normalize done  ok={total_ok:,} bad={total_bad}  ({time.time()-t0:.1f}s)")

    t1 = time.time()
    pattern = str(ndjson_dir / "*.ndjson.gz")
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT
                CAST(record_time AS TIMESTAMP) AS record_time,
                source_ip,
                TRY_CAST(source_port AS INTEGER) AS source_port,
                destination_ip,
                TRY_CAST(destination_port AS INTEGER) AS destination_port,
                method,
                uri,
                host,
                UserAgent AS user_agent
            FROM read_json('{pattern}',
                format = 'newline_delimited',
                ignore_errors = true,
                compression = 'gzip')
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """
    )
    n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    print(f"[flow] wrote {n:,} rows → {out}  ({time.time()-t1:.1f}s)")
    return out


def main() -> None:
    force = "--force" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    if only in (None, "tcpflow"):
        ingest_tcpflow(force=force)
    if only in (None, "flow"):
        ingest_flow(force=force)


if __name__ == "__main__":
    main()
