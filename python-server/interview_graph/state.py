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
        deep_dive_reason: 深掘りすべき理由・内容 (STAY 時に設定、ADVANCE 時は空文字列).
        response_type: evaluate の直近判定種別 (advance/stay/clarify/process).
        non_answer_count: 連続で「回答ではない反応」が続いた回数.
        step_summaries: ステップ番号 → 要約テキスト.
        coverage: ステップ番号 → {target 名: 取得済み要約} のマップ. 未取得 target は空文字 or キー欠落.
        is_complete: 面談が完了したかどうか.
    """

    current_step: int
    messages: Annotated[list[BaseMessage], add_messages]
    deep_dive_count: int
    deep_dive_reason: str
    response_type: str
    non_answer_count: int
    step_summaries: dict[int, str]
    coverage: dict[int, dict[str, str]]
    is_complete: bool
