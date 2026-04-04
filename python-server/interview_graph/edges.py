"""面談グラフの遷移条件.

LLM によるセマンティック判定 + ルールベースのハイブリッド。
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from .state import InterviewState

try:
    from config import MODEL_EVAL
except ImportError:
    MODEL_EVAL = "gpt-4o-mini"

logger = logging.getLogger(__name__)

MAX_DEEP_DIVE = 2

STEP_TRANSITION_PROMPT = """\
あなたは面談進行の判定アシスタントです。
以下のコンテキストと会話履歴を分析し、現在のステップが完了したかどうかを判定してください。

═══ 面談全体の流れ ═══
{all_steps_overview}

═══ これまでに完了したステップの要約 ═══
{completed_summaries}

═══ 現在のステップ情報 ═══
ステップ番号: Step {current_step}（{step_name}）
ステップ目的: {step_purpose}
ステップ指示: {step_instruction}
現ステップでの深掘り回数: {deep_dive_count}/{max_deep_dive}
{prev_deep_dive_info}

═══ 次のステップ ═══
{next_step_info}

═══ 判定基準 ═══
- ステップ指示に記載された確認事項に対して、相手が十分な回答を提供した場合 → ADVANCE
- ステップ目的の達成に必要な情報がまだ不足しており、深掘り回数に余裕がある場合 → STAY
- 深掘り回数が上限に達した場合 → ADVANCE（強制遷移）
- 相手が「話したくない」「答えたくない」と明示した場合 → ADVANCE
- 相手が「分からない」と言い、別角度で1回聞き直し済みの場合 → ADVANCE

以下の JSON 形式で回答してください（JSON のみ、他のテキストは不要）:

ADVANCE の場合:
{{"decision": "ADVANCE"}}

STAY の場合（理由と深掘りすべき内容を必ず含めてください）:
{{"decision": "STAY", "reason": "深掘りすべき理由と具体的な深掘り内容を簡潔に記述"}}
"""


def _build_steps_overview(step_definitions: dict[int, dict[str, str]]) -> str:
    """全ステップの一覧を生成する."""
    lines = []
    for step_num in sorted(step_definitions.keys()):
        s = step_definitions[step_num]
        lines.append(f"  Step {step_num}（{s['name']}）: {s['purpose']}")
    return "\n".join(lines)


def _build_completed_summaries(
    state: InterviewState,
    step_definitions: dict[int, dict[str, str]],
) -> str:
    """完了済みステップの要約を生成する."""
    summaries = state.get("step_summaries", {})
    current = state["current_step"]
    if current <= 1 and not summaries:
        return "（まだ完了したステップはありません）"
    lines = []
    for step_num in range(1, current):
        name = step_definitions.get(step_num, {}).get("name", f"step_{step_num}")
        summary = summaries.get(step_num, summaries.get(str(step_num), "（要約なし）"))
        lines.append(f"  Step {step_num}（{name}）: {summary}")
    return "\n".join(lines) if lines else "（まだ完了したステップはありません）"


async def should_advance(
    state: InterviewState,
    step_definitions: dict[int, dict[str, str]],
    max_step: int = 6,
) -> tuple[Literal["advance", "stay"], str]:
    """現在のステップから次に進むべきか LLM + ルールで判定する.

    Args:
        state: 現在の面談ステート.
        step_definitions: ステップ定義（name, purpose, instruction を含む dict）.
        max_step: closing ステップ番号.

    Returns:
        (decision, reason): decision は "advance" or "stay",
        reason は STAY 時の深掘り理由（ADVANCE 時は空文字列）。
    """
    current_step = state["current_step"]
    deep_dive_count = state["deep_dive_count"]

    if deep_dive_count >= MAX_DEEP_DIVE:
        logger.info(f"Step {current_step}: 深掘り上限 ({MAX_DEEP_DIVE}) 到達 → 強制遷移")
        return ("advance", "")

    if current_step == 0:
        return ("advance", "")

    if current_step >= max_step:
        return ("advance", "")

    # ── コンテキスト構築 ──
    step_def = step_definitions.get(current_step, {})
    step_purpose = step_def.get("purpose", "")
    step_name = step_def.get("name", f"step_{current_step}")
    step_instruction = step_def.get("instruction", "")

    # 次のステップ情報
    next_step_num = current_step + 1
    next_def = step_definitions.get(next_step_num, {})
    if next_def:
        next_step_info = f"Step {next_step_num}（{next_def.get('name', '')}）: {next_def.get('purpose', '')}"
    else:
        next_step_info = "（次のステップはありません — 面談終了）"

    # 前回の深掘り理由
    prev_reason = state.get("deep_dive_reason", "")
    if deep_dive_count > 0 and prev_reason:
        prev_deep_dive_info = f"前回の深掘り理由: {prev_reason}"
    else:
        prev_deep_dive_info = ""

    prompt = STEP_TRANSITION_PROMPT.format(
        all_steps_overview=_build_steps_overview(step_definitions),
        completed_summaries=_build_completed_summaries(state, step_definitions),
        current_step=current_step,
        step_name=step_name,
        step_purpose=step_purpose,
        step_instruction=step_instruction,
        deep_dive_count=deep_dive_count,
        max_deep_dive=MAX_DEEP_DIVE,
        prev_deep_dive_info=prev_deep_dive_info,
        next_step_info=next_step_info,
    )

    recent_messages = list(state["messages"])[-6:]
    llm = ChatOpenAI(model=MODEL_EVAL, temperature=0)
    messages = [SystemMessage(content=prompt)] + recent_messages

    response = await llm.ainvoke(messages)
    raw = response.content.strip()
    logger.info(f"prompt for step {current_step}:\n{prompt}")
    logger.info(f"Step {current_step}: LLM 応答: {raw[:200]}")

    # JSON パース（フォールバック付き）
    try:
        parsed = json.loads(raw)
        decision = parsed.get("decision", "ADVANCE").upper()
        reason = parsed.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        # JSON パース失敗時は従来通りテキストから判定
        logger.warning(f"Step {current_step}: LLM 応答の JSON パース失敗、テキストで判定: {raw[:100]}")
        decision = "ADVANCE" if "ADVANCE" in raw.upper() else "STAY"
        reason = raw if decision == "STAY" else ""

    if decision == "ADVANCE":
        logger.info(f"Step {current_step}: LLM 判定 → ADVANCE")
        return ("advance", "")

    logger.info(f"Step {current_step}: LLM 判定 → STAY (deep_dive {deep_dive_count + 1}, reason={reason})")
    return ("stay", reason)


def route_after_evaluate(state: InterviewState) -> str:
    """evaluate ノード後のルーティング.

    evaluate が current_step を更新済みなので、
    step >= 6 なら closing、それ以外は ask に戻る。
    """
    if state["current_step"] >= 6:
        return "closing"
    return "ask"


def make_route_after_evaluate(max_step: int = 6):
    """max_step を指定可能な route_after_evaluate ファクトリ."""

    def _route(state: InterviewState) -> str:
        if state["current_step"] >= max_step:
            return "closing"
        return "ask"

    return _route
