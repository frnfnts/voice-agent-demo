"""テスト用短縮シナリオ LangGraph StateGraph の構築.

ステップ数を少なくし、STAY/ADVANCE の判定が明確にテストできるよう
各ステップに「特定の言及があれば ADVANCE、なければ STAY」という
基準を設けている。
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .edges import make_route_after_evaluate
from .nodes import (
    ask_node,
    listen_node,
    make_closing_node,
    make_evaluate_node,
    make_greeting_node,
)
from .state import InterviewState

MAX_STEP = 4  # closing ステップ番号

# ─── ステップ定義 ─────────────────────────────────────────

TEST_STEPS: dict[int, dict[str, str]] = {
    0: {
        "name": "greeting",
        "purpose": "面談の目的と所要時間を伝える",
        "instruction": (
            "【Step 0: 挨拶】\n"
            "簡単なテスト面談であることを伝える。\n"
            "3つの質問をすることを説明する。"
        ),
    },
    1: {
        "name": "favorite_food",
        "purpose": "具体的な食べ物の名前が言及されたか確認する（例: カレー、寿司など）。具体的な食べ物名がなければ深掘りする",
        "instruction": (
            "【Step 1: 好きな食べ物】\n"
            "好きな食べ物を1つ教えてもらう。\n"
            "具体的な料理名・食材名を聞き出すこと。"
        ),
    },
    2: {
        "name": "weekend",
        "purpose": "具体的な活動内容が言及されたか確認する（例: 映画を見る、ランニングなど）。具体的な活動名がなければ深掘りする",
        "instruction": (
            "【Step 2: 週末の過ごし方】\n"
            "週末にどんなことをして過ごすか聞く。\n"
            "具体的な活動内容を聞き出すこと。"
        ),
    },
    3: {
        "name": "recent_joy",
        "purpose": "具体的なエピソードが言及されたか確認する（いつ・何があったか）。具体的な出来事がなければ深掘りする",
        "instruction": (
            "【Step 3: 最近うれしかったこと】\n"
            "最近うれしかったことや楽しかったことを聞く。\n"
            "具体的なエピソード（いつ・何があったか）を聞き出すこと。"
        ),
    },
    4: {
        "name": "closing",
        "purpose": "面談の終了を宣言し感謝を伝える",
        "instruction": (
            "【Step 4: 終了】\n"
            "回答への感謝を伝え、面談を終了する。"
        ),
    },
}


def build_test_graph() -> StateGraph:
    """テスト用短縮シナリオの StateGraph を構築して返す（compile はしない）."""
    graph = StateGraph(InterviewState)
    # ── ノード登録 ──
    graph.add_node("greet", make_greeting_node())
    graph.add_node("ask", ask_node)
    graph.add_node("listen", listen_node)
    graph.add_node("evaluate", make_evaluate_node(TEST_STEPS, max_step=MAX_STEP))
    graph.add_node("closing", make_closing_node(closing_step=MAX_STEP))

    # ── エッジ登録 ──
    graph.set_entry_point("greet")
    graph.add_edge("greet", "ask")
    graph.add_edge("ask", "listen")
    graph.add_edge("listen", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        make_route_after_evaluate(max_step=MAX_STEP),
        {"ask": "ask", "closing": "closing"},
    )
    graph.add_edge("closing", END)

    return graph
