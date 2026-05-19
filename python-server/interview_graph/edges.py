"""面談グラフの遷移条件.

LLM によるセマンティック判定 + ルールベースのハイブリッド。
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .state import InterviewState

try:
    from config import MODEL_EVAL
except ImportError:
    MODEL_EVAL = "gpt-4o-mini"

logger = logging.getLogger(__name__)

MAX_DEEP_DIVE = 2
MAX_NON_ANSWER = 2

STEP_TRANSITION_PROMPT = """\
あなたは面談進行の判定アシスタントです。
以下のコンテキストと会話履歴を分析し、現在のステップが完了したかどうかを判定してください。

═══ 面談全体の流れ（▶ が現在のステップ） ═══
{all_steps_overview}

═══ 現在のステップ情報 ═══
ステップ番号: Step {current_step}（{step_name}）
ステップ目的: {step_purpose}
ステップ指示: {step_instruction}
現ステップでの深掘り回数: {deep_dive_count}/{max_deep_dive}
連続非回答反応回数: {non_answer_count}/{max_non_answer}
{prev_deep_dive_info}

═══ 現在のステップでの会話履歴 ═══
{conversation}

═══ 判定基準 ═══
- ステップ指示に記載された確認事項に対して、相手が十分な回答を提供した場合 → ADVANCE
- ステップ目的の達成に必要な情報がまだ不足しており、深掘り回数に余裕がある場合 → STAY
- 深掘り回数が上限に達した場合 → ADVANCE（強制遷移）
- 相手がAIの質問の意味・意図を聞き返している場合 → CLARIFY
- 相手が面談プロセス（記録、共有範囲、進め方、所要時間など）について質問している場合 → PROCESS
- 相手が「話したくない」「答えたくない」と明示した場合 → ADVANCE
- 相手が「分からない」と言い、別角度で1回聞き直し済みの場合 → ADVANCE

以下の JSON 形式で回答してください（JSON のみ、他のテキストは不要）:

ADVANCE の場合:
{{"decision": "ADVANCE"}}

STAY の場合（理由と深掘りすべき内容を必ず含めてください）:
{{"decision": "STAY", "reason": "深掘りすべき理由と具体的な深掘り内容を簡潔に記述。深堀り内容は一度に1点に絞る。"}}\

CLARIFY の場合（ユーザーの聞き返し意図を短く要約してください）:
{{"decision": "CLARIFY", "reason": "ユーザーが何を確認したいか"}}

PROCESS の場合（プロセスに関する質問意図を短く要約してください）:
{{"decision": "PROCESS", "reason": "ユーザーのプロセス質問の要点"}}
"""


def _build_steps_overview(
    step_definitions: dict[int, dict[str, str]],
    current_step: int,
) -> str:
    """全ステップの一覧を生成する（現在地を ▶ で表示）."""
    lines = []
    for step_num in sorted(step_definitions.keys()):
        s = step_definitions[step_num]
        marker = "▶" if step_num == current_step else " "
        lines.append(f"  {marker} Step {step_num}（{s['name']}）: {s['purpose']}")
    return "\n".join(lines)


def _format_conversation(messages: list, limit: int = 10) -> str:
    """直近メッセージを判定用テキストに整形する."""
    recent = messages[-limit:] if len(messages) > limit else messages
    if not recent:
        return "（会話履歴なし）"
    lines = []
    for msg in recent:
        if isinstance(msg, AIMessage):
            role = "面談者（AI）"
        elif isinstance(msg, HumanMessage):
            role = "対象者"
        else:
            role = "system"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


async def should_advance(
    state: InterviewState,
    step_definitions: dict[int, dict[str, str]],
    max_step: int = 6,
) -> tuple[Literal["advance", "stay", "clarify", "process"], str]:
    """現在のステップから次に進むべきか LLM + ルールで判定する.

    Args:
        state: 現在の面談ステート.
        step_definitions: ステップ定義（name, purpose, instruction を含む dict）.
        max_step: closing ステップ番号.

    Returns:
        (decision, reason): decision は "advance"/"stay"/"clarify"/"process",
        reason は STAY 時の深掘り理由（ADVANCE 時は空文字列）。
    """
    current_step = state["current_step"]
    deep_dive_count = state["deep_dive_count"]
    non_answer_count = state.get("non_answer_count", 0)

    if non_answer_count >= MAX_NON_ANSWER:
        logger.info(
            f"Step {current_step}: 非回答反応上限 ({MAX_NON_ANSWER}) 到達 → STAY で本題に戻す"
        )
        return ("stay", "本題の回答がまだ不足しています。現在の質問に1点だけ具体的に答えてもらってください。")

    if deep_dive_count >= MAX_DEEP_DIVE:
        logger.debug(f"Step {current_step}: 深掘り上限 ({MAX_DEEP_DIVE}) 到達 → 強制遷移")
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

    # 前回の深掘り理由
    prev_reason = state.get("deep_dive_reason", "")
    if deep_dive_count > 0 and prev_reason:
        prev_deep_dive_info = f"前回の深掘り理由: {prev_reason}"
    else:
        prev_deep_dive_info = ""

    prompt = STEP_TRANSITION_PROMPT.format(
        all_steps_overview=_build_steps_overview(step_definitions, current_step),
        current_step=current_step,
        step_name=step_name,
        step_purpose=step_purpose,
        step_instruction=step_instruction,
        deep_dive_count=deep_dive_count,
        max_deep_dive=MAX_DEEP_DIVE,
        non_answer_count=non_answer_count,
        max_non_answer=MAX_NON_ANSWER,
        prev_deep_dive_info=prev_deep_dive_info,
        conversation=_format_conversation(list(state["messages"]), limit=10),
    )

    llm = ChatOpenAI(
        model=MODEL_EVAL,
        temperature=0,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    response = await llm.ainvoke([SystemMessage(content=prompt)])
    raw = response.content.strip()
    logger.debug(f"prompt for step {current_step}:\n{prompt}")
    logger.debug(f"Step {current_step}: LLM 応答: {raw[:200]}")

    # JSON パース（フォールバック付き）
    try:
        parsed = json.loads(raw)
        decision = parsed.get("decision", "ADVANCE").upper()
        reason = parsed.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        # JSON パース失敗時は従来通りテキストから判定
        logger.warning(f"Step {current_step}: LLM 応答の JSON パース失敗、テキストで判定: {raw[:100]}")
        upper = raw.upper()
        if "CLARIFY" in upper:
            decision = "CLARIFY"
        elif "PROCESS" in upper:
            decision = "PROCESS"
        elif "ADVANCE" in upper:
            decision = "ADVANCE"
        else:
            decision = "STAY"
        reason = raw if decision == "STAY" else ""

    if decision == "ADVANCE":
        logger.debug(f"Step {current_step}: LLM 判定 → ADVANCE")
        return ("advance", "")

    if decision == "CLARIFY":
        logger.info(f"Step {current_step}: LLM 判定 → CLARIFY")
        return ("clarify", reason)

    if decision == "PROCESS":
        logger.info(f"Step {current_step}: LLM 判定 → PROCESS")
        return ("process", reason)

    logger.info(f"Step {current_step}: LLM 判定 → STAY (deep_dive {deep_dive_count + 1}, reason={reason})")
    return ("stay", reason)


def make_route_after_evaluate(max_step: int = 6):
    """max_step を指定可能な route_after_evaluate ファクトリ."""

    def _route(state: InterviewState) -> str:
        if state["current_step"] >= max_step:
            return "closing"
        return "ask"

    return _route
