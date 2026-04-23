"""Automated analysis mode: agent team discusses both tasks end-to-end.

Usage:
  python main_analyze.py               # full pipeline (both tasks)
  python main_analyze.py --task 1      # asset discovery only
  python main_analyze.py --task 2      # anomaly detection only
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)  # 实时看见 agent 输出

from agents import build_team
from config import DATA_PROCESSED, TRANSCRIPTS

TASK_1_PROMPT = """目标:企业内部网络资产识别 + 通信模式刻画(任务1)。

可用数据:tcpflow 表(record_time, source_ip, destination_ip, protocol, destination_port, uplink_length, downlink_length)。

请 Moderator 依次围绕**资产画像**提出 2-3 个真正值得辩论的假设(错判代价高、证据不直观)。覆盖方向建议:
- 高流量/高连接异常节点的角色定性(是否核心服务端、代理、同步节点、扫描器)
- 非标准端口(如 8360、7006、5001 等)承载的服务性质
- 整体流量画像(内外网比例、服务端分布、时段特征)

简单的事实(总行数、时间范围、top 端口列表)**直接在 Poser 环节用 SQL 写清楚**,**不要**塞给 Pro/Con 辩。
每个假设辩完 [[VERDICT]] 进入下一题,全部辩完 [[DEBATE_DONE]] 附**完整资产清单表 + 通信模式总结**。
"""

TASK_2_PROMPT = """目标:网络威胁/异常通信识别 + 攻击过程描述(任务2)。

可用数据:flow 表(record_time, source_ip, source_port, destination_ip, destination_port, method, uri, host, user_agent)。

请 Moderator 依次围绕**威胁发现**提出 2-3 个真正值得辩论的假设。覆盖方向建议:
- 最可疑的 source_ip / UA / URI 模式(是攻击还是噪音?)
- 时序 / 4xx / 异常 method 风暴是否构成扫描行为
- 能否串出一条完整攻击链(侦察 → 尝试 → 突破 → 横移 / 外传)

Poser 要先用 SQL / Python 扫 uri 里的可疑 payload、统计异常 UA,再挑最有争议的拿来辩。
每题辩完 [[VERDICT]] 下一题,全部辩完 [[DEBATE_DONE]] 附**威胁清单表 + 攻击链叙述**。
"""


async def run(task_prompt: str, label: str, max_messages: int, task_mode: str) -> None:
    team = build_team(task="analyze", max_messages=max_messages, task_mode=task_mode)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    transcript_path = TRANSCRIPTS / f"{stamp}-{label}.jsonl"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[team] start: {label}")
    print(f"[team] transcript → {transcript_path}")

    messages: list[dict] = []
    with transcript_path.open("w", encoding="utf-8") as f:
        async for msg in team.run_stream(task=task_prompt):
            # Console-compatible pretty print for user
            source = getattr(msg, "source", "?")
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = str(content)
            print(f"\n── {source} ──\n{content}\n")
            messages.append(
                {
                    "ts": dt.datetime.now().isoformat(),
                    "source": source,
                    "type": type(msg).__name__,
                    "content": content if isinstance(content, str) else str(content),
                }
            )
            f.write(json.dumps(messages[-1], ensure_ascii=False) + "\n")
            f.flush()

    print(f"[team] done. transcript: {transcript_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["1", "2", "all"], default="all")
    ap.add_argument("--max-messages", type=int, default=40)
    args = ap.parse_args()

    if not (DATA_PROCESSED / "tcpflow.parquet").exists() and args.task in ("1", "all"):
        raise SystemExit(
            "tcpflow.parquet missing. Run: python ingest.py --only tcpflow"
        )
    if not (DATA_PROCESSED / "flow.parquet").exists() and args.task in ("2", "all"):
        raise SystemExit(
            "flow.parquet missing. Run: python ingest.py --only flow"
        )

    if args.task in ("1", "all"):
        asyncio.run(run(TASK_1_PROMPT, "task1-assets", args.max_messages, "asset"))
    if args.task in ("2", "all"):
        asyncio.run(run(TASK_2_PROMPT, "task2-anomalies", args.max_messages, "threat"))


if __name__ == "__main__":
    main()
