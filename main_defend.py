"""答辩模式 —— 你扮演老师提问,agent team 协作答辩。

Usage:
  python main_defend.py                           # 交互输入问题
  python main_defend.py -q "为什么这个IP是数据库?" # 单问
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

sys.stdout.reconfigure(line_buffering=True)

from agents import build_team
from config import TRANSCRIPTS

DEFEND_PROMPT_TEMPLATE = """老师的提问:{question}

请以**辩论形式**深度作答,流程:
- **Moderator(Poser+Judge)**:把问题转成一个清晰的二元假设作为本次唯一议题 + 初步数据探索
- **Pro / Con** 并行独立开场
- 至少一轮反驳后,Moderator 给 [[VERDICT]] → 上诉环节 → 若无上诉则 [[DEBATE_DONE]]
- 最终输出必须包含明确的业务结论(不是"各有道理"),附资产/威胁清单表

所有关键数字必须有 run_sql / run_python 来源。
"""


async def defend(
    question: str, max_messages: int = 25, task_mode: str = "threat"
) -> None:
    # In defend mode, the user's question IS the hypothesis — skip Moderator pose
    team = build_team(
        task="defend",
        max_messages=max_messages,
        task_mode=task_mode,
        skip_initial_pose=True,
    )

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = TRANSCRIPTS / f"{stamp}-defend.jsonl"

    print(f"[defend] question: {question}")
    print(f"[defend] transcript → {path}")

    with path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"ts": stamp, "question": question}, ensure_ascii=False
            )
            + "\n"
        )
        async for msg in team.run_stream(
            task=DEFEND_PROMPT_TEMPLATE.format(question=question)
        ):
            source = getattr(msg, "source", "?")
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = str(content)
            print(f"\n── {source} ──\n{content}\n")
            f.write(
                json.dumps(
                    {
                        "ts": dt.datetime.now().isoformat(),
                        "source": source,
                        "content": content if isinstance(content, str) else str(content),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            f.flush()
    print(f"[defend] done: {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question", help="一句话提问;留空则交互输入")
    ap.add_argument("--max-messages", type=int, default=25)
    ap.add_argument(
        "--mode",
        choices=["asset", "threat", "auto"],
        default="threat",
        help="任务模式:threat 启用威胁偏置(默认)",
    )
    args = ap.parse_args()

    if args.question:
        asyncio.run(defend(args.question, args.max_messages, args.mode))
        return

    print("答辩模式。输入问题,回车后 agent team 会协作回答。输入 exit 退出。")
    while True:
        try:
            q = input("\n老师 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q in {"", "exit", "quit"}:
            break
        asyncio.run(defend(q, args.max_messages, args.mode))


if __name__ == "__main__":
    main()
