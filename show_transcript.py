"""Live transcript viewer.

Usage:
  python show_transcript.py                    # 最新一份,一次性打印
  python show_transcript.py -f                 # follow 当前最新(固定文件)
  python show_transcript.py -w                 # watch 模式:自动切到新开的 transcript
  python show_transcript.py --brief            # 只看 TextMessage,隐藏 Thought/Tool
  python show_transcript.py <path>             # 指定具体文件
  python show_transcript.py --only Critic      # 只看某个 agent
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from config import TRANSCRIPTS

# ANSI colors
COLORS = {
    "QuestionPoser": "\033[1;36m",  # bright cyan
    "ProDebater":    "\033[1;32m",  # bright green
    "ConDebater":    "\033[1;31m",  # bright red
    "Judge":         "\033[1;33m",  # bright yellow
    # legacy 5-agent names
    "DataEngineer":  "\033[36m",
    "AssetAnalyst":  "\033[32m",
    "ThreatHunter":  "\033[31m",
    "Visualizer":    "\033[35m",
    "Critic":        "\033[33m",
    "user":          "\033[37m",
}
DIM = "\033[2m"
RESET = "\033[0m"

TEXT_TYPES = {"TextMessage"}
INTERESTING_TYPES = {
    "TextMessage",
    "ThoughtEvent",
    "ToolCallSummaryMessage",
    "ToolCallRequestEvent",
}


def latest_transcript() -> Path | None:
    files = sorted(TRANSCRIPTS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def render(rec: dict, brief: bool, only: str | None) -> None:
    src = rec.get("source", "?")
    typ = rec.get("type", "")
    if only and src != only:
        return
    if brief and typ not in TEXT_TYPES:
        return
    if not brief and typ not in INTERESTING_TYPES:
        return

    color = COLORS.get(src, "")
    content = rec.get("content", "")
    if not isinstance(content, str):
        content = str(content)

    # Prettify ToolCallRequestEvent to one-line summary
    if typ == "ToolCallRequestEvent":
        # content looks like: "[FunctionCall(id='...', arguments='{...}', name='run_sql')]"
        short = content[:200].replace("\n", " ") + ("..." if len(content) > 200 else "")
        print(f"{DIM}  [{src}] tool_call: {short}{RESET}")
        return

    if typ == "ThoughtEvent":
        if len(content) > 600:
            content = content[:600] + "\n...<thought truncated>"
        print(f"{color}{DIM}── {src} 💭 ──{RESET}")
        print(f"{DIM}{content}{RESET}")
        print()
        return

    # Tool summary: show but truncate tight
    if typ == "ToolCallSummaryMessage":
        short = content[:1000] + ("\n...<tool result truncated>" if len(content) > 1000 else "")
        print(f"{color}{DIM}── {src} 🔧 ──{RESET}")
        print(f"{DIM}{short}{RESET}")
        print()
        return

    # Full text message
    if len(content) > 4000:
        content = content[:4000] + "\n...<truncated>"
    print(f"{color}── {src} ──{RESET}")
    print(content)
    print()


def tail_file(path: Path, brief: bool, only: str | None) -> None:
    """Read to end, then keep following. Stops on KeyboardInterrupt."""
    with path.open(encoding="utf-8") as f:
        # Initial drain
        for line in f:
            line = line.rstrip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            render(rec, brief, only)
        # Follow
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                time.sleep(0.3)
                f.seek(pos)
                continue
            line = line.rstrip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            render(rec, brief, only)


def watch_loop(brief: bool, only: str | None) -> None:
    """Always follow whatever the newest transcript is.

    When a newer file appears mid-tail, switch to it.
    """
    current: Path | None = None
    print(f"{DIM}[watch mode] 监听 {TRANSCRIPTS}/,自动切换到最新 transcript{RESET}\n")
    while True:
        latest = latest_transcript()
        if latest is None:
            time.sleep(1)
            continue
        if latest != current:
            if current is not None:
                print(f"{DIM}\n── 切到新 transcript: {latest.name} ──\n{RESET}")
            else:
                print(f"{DIM}# {latest}\n{RESET}")
            current = latest
        try:
            with current.open(encoding="utf-8") as f:
                # print existing content
                for line in f:
                    line = line.rstrip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    render(rec, brief, only)
                while True:
                    # check if a newer transcript has appeared
                    newest = latest_transcript()
                    if newest and newest != current:
                        break  # outer loop will switch
                    pos = f.tell()
                    line = f.readline()
                    if not line:
                        time.sleep(0.3)
                        f.seek(pos)
                        continue
                    line = line.rstrip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    render(rec, brief, only)
        except FileNotFoundError:
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="transcript path (default: latest)")
    ap.add_argument("-f", "--follow", action="store_true",
                    help="follow the fixed file (tail -F)")
    ap.add_argument("-w", "--watch", action="store_true",
                    help="watch mode: auto-switch to the newest transcript as runs start")
    ap.add_argument("--brief", action="store_true",
                    help="only show TextMessage (hide thought + tool summaries)")
    ap.add_argument("--only", help="filter to a single agent name")
    args = ap.parse_args()

    try:
        if args.watch:
            watch_loop(args.brief, args.only)
            return

        path = Path(args.path) if args.path else latest_transcript()
        if path is None:
            sys.exit("no transcripts yet")
        if not path.exists():
            sys.exit(f"not found: {path}")

        print(f"{DIM}# {path}\n{RESET}")
        if args.follow:
            tail_file(path, args.brief, args.only)
        else:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    render(rec, args.brief, args.only)
    except KeyboardInterrupt:
        print(f"\n{DIM}[exit]{RESET}")


if __name__ == "__main__":
    main()
