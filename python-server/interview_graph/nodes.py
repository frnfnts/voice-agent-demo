"""面談ステップごとのノード関数.

各ノードは InterviewState を受け取り、LLM を呼び出してレスポンスを生成し、
更新された state を返す。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .state import InterviewState

logger = logging.getLogger(__name__)

# ─── LLM ───────────────────────────────────────────────
_llm = None


def get_llm() -> ChatOpenAI:
    """Lazy-init LLM (requires OPENAI_API_KEY env var)."""
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    return _llm


# ─── 共通ヘルパー ────────────────────────────────────────


def _build_step_system_prompt(step_instruction: str, full_prompt: str) -> str:
    """ステップ固有の指示を full_prompt に追加して返す."""
    return (
        f"{full_prompt}\n\n"
        f"---\n"
        f"【現在のステップの指示】\n{step_instruction}\n"
        f"このステップの指示に集中して回答してください。"
    )


def make_step_node(
    step_num: int,
    step_instruction: str,
    full_prompt: str,
):
    """汎用ステップノードのファクトリ.

    Args:
        step_num: ステップ番号 (0-6).
        step_instruction: このステップ固有の指示テキスト.
        full_prompt: シナリオ全体のプロンプト.

    Returns:
        LangGraph ノード関数.
    """

    async def _node(state: InterviewState) -> dict[str, Any]:
        llm = get_llm()
        system_prompt = _build_step_system_prompt(step_instruction, full_prompt)

        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

        response = await llm.ainvoke(messages)

        return {
            "messages": [response],
            "current_step": step_num,
        }

    _node.__name__ = f"step_{step_num}"
    return _node


def make_greeting_node(full_prompt: str, greeting_instruction: str):
    """Step 0 (挨拶・趣旨説明) ノード.

    初回なので deep_dive_count をリセットし、is_complete=False を明示する。
    """

    async def greeting_node(state: InterviewState) -> dict[str, Any]:
        llm = get_llm()
        system_prompt = _build_step_system_prompt(greeting_instruction, full_prompt)
        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

        response = await llm.ainvoke(messages)

        return {
            "messages": [response],
            "current_step": 0,
            "deep_dive_count": 0,
            "is_complete": False,
        }

    return greeting_node


def make_closing_node(full_prompt: str, closing_instruction: str):
    """Step 6 (終了) ノード. is_complete=True にする."""

    async def closing_node(state: InterviewState) -> dict[str, Any]:
        llm = get_llm()
        system_prompt = _build_step_system_prompt(closing_instruction, full_prompt)
        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

        response = await llm.ainvoke(messages)

        # 各ステップのサマリーをコピーして step_summaries に追加
        summaries = dict(state.get("step_summaries", {}))

        return {
            "messages": [response],
            "current_step": 6,
            "deep_dive_count": 0,
            "is_complete": True,
            "step_summaries": summaries,
        }

    return closing_node
