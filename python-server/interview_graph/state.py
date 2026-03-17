"""面談の状態定義."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class InterviewState(TypedDict):
    """LangGraph で管理する面談ステート.

    Attributes:
        current_step: 現在の面談ステップ (0-6).
        messages: 会話履歴 (LangChain message 形式). add_messages reducer で追記.
        deep_dive_count: 現ステップ内での深掘り回数.
        step_summaries: ステップ番号 → 要約テキスト.
        is_complete: 面談が完了したかどうか.
    """

    current_step: int
    messages: Annotated[list[BaseMessage], add_messages]
    deep_dive_count: int
    step_summaries: dict[int, str]
    is_complete: bool
