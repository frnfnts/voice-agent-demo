"""面談ステップ間の遷移条件.

LLM によるセマンティック判定 + ルールベースのハイブリッド。
"""

from __future__ import annotations

import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .state import InterviewState

logger = logging.getLogger(__name__)

# deep_dive の上限
MAX_DEEP_DIVE = 2

STEP_TRANSITION_PROMPT = """\
あなたは面談進行の判定アシスタントです。
以下の会話履歴を分析し、現在のステップが完了したかどうかを判定してください。

現在のステップ: Step {current_step}
ステップ目的: {step_purpose}
現ステップでの深掘り回数: {deep_dive_count}/{max_deep_dive}

判定基準:
- 相手が質問に対して回答を提供し、さらなる深掘りが不要な場合 → ADVANCE
- 回答が不十分だがまだ深掘り回数に余裕がある場合 → STAY
- 深掘り回数が上限に達した場合 → ADVANCE（強制遷移）
- 相手が「話したくない」「答えたくない」と明示した場合 → ADVANCE
- 相手が「分からない」と言い、別角度で1回聞き直し済みの場合 → ADVANCE

以下のいずれか1語だけ回答してください:
ADVANCE - 次のステップに進む
STAY - 現在のステップを継続する
"""


async def should_advance(
    state: InterviewState,
    step_purposes: dict[int, str],
) -> Literal["advance", "stay"]:
    """現在のステップから次に進むべきか LLM + ルールで判定する.

    Returns:
        "advance" or "stay"
    """
    current_step = state["current_step"]
    deep_dive_count = state["deep_dive_count"]

    # ルールベース: 強制遷移
    if deep_dive_count >= MAX_DEEP_DIVE:
        logger.info(
            f"Step {current_step}: 深掘り上限 ({MAX_DEEP_DIVE}) 到達 → 強制遷移"
        )
        return "advance"

    # Step 0 (挨拶) は常に1ターンで advance
    if current_step == 0:
        return "advance"

    # Step 6 (終了) が実行されたらもう進まない
    if current_step >= 6:
        return "advance"

    # LLM 判定
    step_purpose = step_purposes.get(current_step, "")
    prompt = STEP_TRANSITION_PROMPT.format(
        current_step=current_step,
        step_purpose=step_purpose,
        deep_dive_count=deep_dive_count,
        max_deep_dive=MAX_DEEP_DIVE,
    )

    # 直近の会話のみ送る（コスト削減）
    recent_messages = list(state["messages"])[-6:]
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    messages = [SystemMessage(content=prompt)] + recent_messages

    response = await llm.ainvoke(messages)
    decision = response.content.strip().upper()

    if decision == "ADVANCE":
        logger.info(f"Step {current_step}: LLM 判定 → ADVANCE")
        return "advance"

    logger.info(f"Step {current_step}: LLM 判定 → STAY (deep_dive {deep_dive_count + 1})")
    return "stay"


def make_transition_edge(step_purposes: dict[int, str], next_step_name: str):
    """conditional_edge 用のルーター関数を生成する.

    Args:
        step_purposes: ステップ番号 → 目的テキスト のマップ.
        next_step_name: 次のステップのノード名.

    Returns:
        async router function for conditional edges.
    """

    async def router(state: InterviewState) -> str:
        decision = await should_advance(state, step_purposes)
        current_node = f"step_{state['current_step']}"
        if decision == "advance":
            return next_step_name
        # STAY: deep_dive_count を増やして同じノードに戻る
        return current_node

    return router
