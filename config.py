"""Qwen client + path constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_RAW_TCPFLOW = ROOT.parent / "01资产识别" / "dataset_1" / "tcpflow"
DATA_RAW_FLOW_DIR = ROOT.parent / "02异常分析"
DATA_PROCESSED = ROOT / "data_processed"
OUTPUTS = ROOT / "outputs"
TRANSCRIPTS = ROOT / "transcripts"

for d in (DATA_PROCESSED, OUTPUTS, TRANSCRIPTS):
    d.mkdir(parents=True, exist_ok=True)

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_HEAVY = os.getenv("QWEN_MODEL_HEAVY", "qwen3.6-plus")
QWEN_MODEL_LIGHT = os.getenv("QWEN_MODEL_LIGHT", "qwen3.6-flash")


def _qwen_model_info() -> dict:
    return {
        "vision": False,
        "function_calling": True,
        "json_output": True,
        "family": "unknown",
        "structured_output": False,
    }


def make_client(model: str | None = None):
    """Build an OpenAI-compatible client pointing at Dashscope."""
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    api_key = (
        os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("VLM_API_KEY")
        or os.environ.get("QWEN_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "API key not set — put DASHSCOPE_API_KEY (or VLM_API_KEY) in .env"
        )

    return OpenAIChatCompletionClient(
        model=model or QWEN_MODEL_HEAVY,
        base_url=QWEN_BASE_URL,
        api_key=api_key,
        model_info=_qwen_model_info(),
        max_retries=5,
        timeout=120.0,
    )
