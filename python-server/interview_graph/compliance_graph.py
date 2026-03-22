"""コンプライアンス通報受付面談用 LangGraph StateGraph の構築."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .edges import route_after_evaluate
from .nodes import (
    ask_node,
    listen_node,
    make_closing_node,
    make_evaluate_node,
    make_greeting_node,
)
from .state import InterviewState

# ─── ステップ定義 ─────────────────────────────────────────

COMPLIANCE_STEPS: dict[int, dict[str, str]] = {
    0: {
        "name": "greeting",
        "purpose": "窓口の役割・秘密厳守・通報者保護を伝え、安心感を与える",
        "instruction": (
            "【Step 0: 趣旨説明と安心確保】\n"
            "必ず以下を伝えること:\n"
            "1. この窓口はコンプライアンス違反の通報を受け付ける場であること\n"
            "2. 通報者の氏名・所属は厳格に秘密が守られること\n"
            "3. 公益通報者保護法に基づき不利益な取扱いは一切ないこと\n"
            "4. 面談内容は調査に必要な最小限の関係者にのみ共有されること\n"
            "5. 所要時間の目安（15〜20分程度）\n"
            "禁止: 雑談や世間話から始めてはいけない。"
        ),
    },
    1: {
        "name": "overview",
        "purpose": "どのような問題について通報したいのか大枠を把握する",
        "instruction": (
            "【Step 1: 通報の概要把握】\n"
            "どのようなことがあったのか概要を聞く。\n"
            "この段階では詳細を求めすぎない。全体像の理解を優先する。"
        ),
    },
    2: {
        "name": "five_w_one_h",
        "purpose": "通報内容の具体的な事実関係を5W1Hで明確にする",
        "instruction": (
            "【Step 2: 事実確認（5W1H）】\n"
            "以下の要素を漏れなく確認する（自然な流れで一度に全部聞かない）:\n"
            "- When（いつ）: いつ頃か？ 1回か継続か？\n"
            "- Where（どこで）: どの部署・拠点・場所か？\n"
            "- Who（誰が）: 関与者は？ 実行者・指示者・黙認者\n"
            "- What（何を）: 具体的な行為は？\n"
            "- Why/How（なぜ・どのように）: 経緯は？ 組織的か個人的か？\n"
            "答えたくない場合は無理に聞かない。\n"
            "Step 2 終了前に未確認の5W1H要素がないかチェックする。"
        ),
    },
    3: {
        "name": "evidence",
        "purpose": "調査に必要な証拠の有無と入手可能性を確認する",
        "instruction": (
            "【Step 3: 証拠・裏付けの確認】\n"
            "記録や証拠（メール、チャット、書類、写真等）の有無を確認する。\n"
            "目撃者がいるか確認する。\n"
            "証拠がなくても通報として受理できることを必ず伝える。"
        ),
    },
    4: {
        "name": "impact",
        "purpose": "被害の規模や影響を把握する",
        "instruction": (
            "【Step 4: 影響範囲の確認】\n"
            "他に影響を受けている人がいるか確認する。\n"
            "業務やお客様への影響を確認する。\n"
            "現在も続いているか確認する。\n"
            "通報者が被害者の場合は心理的安全を最優先にする。"
        ),
    },
    5: {
        "name": "hopes_and_fears",
        "purpose": "通報者がどのような対応を望んでいるか、不安を把握する",
        "instruction": (
            "【Step 5: 通報者の希望・懸念の確認】\n"
            "会社にどのような対応を望んでいるかを聞く。\n"
            "通報による不安・懸念を確認する。\n"
            "調査で配慮してほしいことを確認する。"
        ),
    },
    6: {
        "name": "closing",
        "purpose": "要点を整理し、今後の流れを説明して面談を終了する",
        "instruction": (
            "【Step 6: 終了】\n"
            "通報内容の核心を1〜2文で確認する。\n"
            "今後の流れ（調査実施・進捗報告・追加連絡可）を伝える。\n"
            "終了後の追加発言は禁止。"
        ),
    },
}


def build_compliance_graph() -> StateGraph:
    """コンプライアンス通報面談の StateGraph を構築して返す（compile はしない）."""
    graph = StateGraph(InterviewState)

    # ── ノード登録 ──
    graph.add_node("greet", make_greeting_node())
    graph.add_node("ask", ask_node)
    graph.add_node("listen", listen_node)
    graph.add_node("evaluate", make_evaluate_node(COMPLIANCE_STEPS))
    graph.add_node("closing", make_closing_node())

    # ── エッジ登録 ──
    graph.set_entry_point("greet")
    graph.add_edge("greet", "ask")
    graph.add_edge("ask", "listen")
    graph.add_edge("listen", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"ask": "ask", "closing": "closing"},
    )
    graph.add_edge("closing", END)

    return graph
