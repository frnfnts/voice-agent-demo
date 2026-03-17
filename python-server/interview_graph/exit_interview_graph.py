"""退職面談用 LangGraph StateGraph の構築."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .edges import make_transition_edge
from .nodes import make_closing_node, make_greeting_node, make_step_node
from .state import InterviewState

# ─── ステップ定義 ─────────────────────────────────────────

EXIT_INTERVIEW_STEPS: dict[int, dict[str, str]] = {
    0: {
        "name": "greeting",
        "purpose": "面談の目的・守秘義務・所要時間を伝え、安心感を与える",
        "instruction": (
            "【Step 0: 趣旨説明と挨拶】\n"
            "必ず以下を伝えること:\n"
            "1. 今日の面談の目的（会社の改善のために率直な意見を聞きたい）\n"
            "2. 引き留めや慰留が目的ではないこと\n"
            "3. 話した内容は人事部内に限り、個人を特定する形では使わないこと\n"
            "4. 所要時間の目安（10〜15分程度）\n"
            "禁止: アイスブレイクから始めてはいけない。"
        ),
    },
    1: {
        "name": "why_joined",
        "purpose": "ポジティブだった原点を確認する",
        "instruction": (
            "【Step 1: 入社理由の振り返り】\n"
            "この会社に入社を決めた理由や、期待していたことを聞く。"
        ),
    },
    2: {
        "name": "gap",
        "purpose": "期待と現実の乖離（組織の問題点）を探る",
        "instruction": (
            "【Step 2: 入社後のギャップ】\n"
            "実際に働いてみて、入社前のイメージと違った部分を聞く。\n"
            "ギャップが「特にない」場合は無理に掘り下げず次へ。"
        ),
    },
    3: {
        "name": "trigger",
        "purpose": "最初に離職を意識した具体的な事象を特定する",
        "instruction": (
            "【Step 3: 退職のきっかけ】\n"
            "退職を考え始めたきっかけを聞く。"
        ),
    },
    4: {
        "name": "decision",
        "purpose": "最終的な決断に至った本質的な理由を聞き出す",
        "instruction": (
            "【Step 4: 退職の決め手】\n"
            "最終的に退職を決意した一番の理由を聞く。\n"
            "複数の理由が出た場合はすべてについて確認する。"
        ),
    },
    5: {
        "name": "improvement",
        "purpose": "組織改善に繋がるヒントを得る",
        "instruction": (
            "【Step 5: 改善可能性の確認】\n"
            "会社側で改善できていたら結果が変わっていた可能性を聞く。"
        ),
    },
    6: {
        "name": "closing",
        "purpose": "面談の終了を明確に宣言し、感謝を伝える",
        "instruction": (
            "【Step 6: 終了】\n"
            "感謝を伝え、面談を終了する。\n"
            "終了後の追加発言は禁止。"
        ),
    },
}


def build_exit_interview_graph(full_prompt: str) -> StateGraph:
    """退職面談の StateGraph を構築して compile 済みグラフを返す.

    Args:
        full_prompt: prompt.txt から読み込んだ全文プロンプト.

    Returns:
        Compiled LangGraph.
    """
    graph = StateGraph(InterviewState)

    step_purposes = {k: v["purpose"] for k, v in EXIT_INTERVIEW_STEPS.items()}
    steps = EXIT_INTERVIEW_STEPS

    # ── ノード登録 ──
    graph.add_node(
        "step_0",
        make_greeting_node(full_prompt, steps[0]["instruction"]),
    )
    for i in range(1, 6):
        graph.add_node(
            f"step_{i}",
            make_step_node(i, steps[i]["instruction"], full_prompt),
        )
    graph.add_node(
        "step_6",
        make_closing_node(full_prompt, steps[6]["instruction"]),
    )

    # ── エッジ登録 ──
    graph.set_entry_point("step_0")

    # Step 0 → Step 1 (常に)
    graph.add_edge("step_0", "step_1")

    # Step 1〜5: conditional edges (STAY or ADVANCE)
    for i in range(1, 6):
        next_node = f"step_{i + 1}"
        current_node = f"step_{i}"
        router = make_transition_edge(step_purposes, next_node)
        graph.add_conditional_edges(
            current_node,
            router,
            {next_node: next_node, current_node: current_node},
        )

    # Step 6 → END
    graph.add_edge("step_6", END)

    return graph.compile()
