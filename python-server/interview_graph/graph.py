"""汎用面談グラフビルダー.

全シナリオ共通のトポロジー (greet → ask → evaluate → {ask|closing} → END) を構築する。
シナリオごとの差分はステップ定義 (step_definitions) と max_step で吸収する。
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .edges import make_route_after_evaluate
from .nodes import (
    ask_node,
    make_closing_node,
    make_evaluate_node,
    make_greeting_node,
)
from .state import InterviewState


def build_interview_graph(
    step_definitions: dict[int, dict[str, str]],
    max_step: int | None = None,
) -> StateGraph:
    """ステップ定義に基づいて面談グラフを構築する（compile はしない）.

    Args:
        step_definitions: ステップ番号 → {name, purpose, instruction} の辞書.
        max_step: closing ステップ番号. None の場合は step_definitions の最大キーを使う.
    """
    if max_step is None:
        max_step = max(step_definitions.keys())

    graph = StateGraph(InterviewState)

    # ── ノード登録 ──
    graph.add_node("greet", make_greeting_node())
    graph.add_node("ask", ask_node)
    graph.add_node("evaluate", make_evaluate_node(step_definitions, max_step=max_step))
    graph.add_node("closing", make_closing_node(closing_step=max_step))

    # ── エッジ登録 ──
    graph.set_entry_point("greet")
    graph.add_edge("greet", "ask")
    graph.add_edge("ask", "evaluate")  # interrupt_after="ask" でここで一時停止
    graph.add_conditional_edges(
        "evaluate",
        make_route_after_evaluate(max_step=max_step),
        {"ask": "ask", "closing": "closing"},
    )
    graph.add_edge("closing", END)

    return graph
