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
同時に、直前のユーザー応答が新たにどの取得項目（target）を埋めたかも抽出してください。

═══ 面談全体の流れ（▶ が現在のステップ） ═══
{all_steps_overview}

═══ 現在のステップ情報 ═══
ステップ番号: Step {current_step}（{step_name}）
ステップ目的: {step_purpose}
ステップ指示: {step_instruction}
現ステップでの深掘り回数: {deep_dive_count}/{max_deep_dive}
連続非回答反応回数: {non_answer_count}/{max_non_answer}
{prev_deep_dive_info}

═══ 現ステップの取得項目（targets）と取得状況 ═══
{targets_and_coverage}

═══ 現在のステップでの会話履歴 ═══
{conversation}

═══ 判定基準 ═══
- ステップに targets がある場合、**全ての target が ✅（取得済）** で、十分な具体性があれば → ADVANCE
- targets に ❌（未取得）が残っており、深掘り回数に余裕がある場合 → STAY（未取得項目を聞く方向で）
- 深掘り回数が上限に達した場合 → ADVANCE（強制遷移）
- 相手がAIの質問の意味・意図を聞き返している場合 → CLARIFY
- 相手が面談プロセス（記録、共有範囲、進め方、所要時間など）について質問している場合 → PROCESS
- 相手が「話したくない」「答えたくない」と明示した場合 → ADVANCE
- 相手が「分からない」と言い、別角度で1回聞き直し済みの場合 → ADVANCE
- targets が空のステップ（greeting/closing 等）はステップ指示と会話履歴で判断する
- **時期・タイミングを尋ねて「覚えていない」「分からない」と返ってきた場合**: 同じ時系列視点での再質問は禁止。別の視点（具体エピソード・感情・比較など）に切り替えること

═══ STAY の場合の「深掘り視点」選択 ═══
STAY と判定する場合、次に AI が質問する角度を以下の 6 つの視点から **必ず 1 つ** 選んでください。
**最優先のルール**: 未取得 target（❌）がある場合、その target を埋めるのに **最も自然な視点** を選ぶこと。
無理に視点をローテーションしないこと（不適切な視点で聞くと会話が壊れる）。

**視点選択の禁止事項**:
- 直前に時系列視点を使い、ユーザーが「覚えていない」「分からない」と答えた場合 → 時系列は禁止、他の視点を選ぶ
- ユーザーが既に感情的ネガティブ影響（「きつい」「疲れる」「つらい」など）を表明している場合 → 影響視点は冗長になる。量・頻度・原因・背景を把握できる具体エピソード・比較・時系列を優先すること

  - 時系列     : いつ頃から / どれぐらい前から / 最初に感じたタイミングは
                  例「それを意識し始めたのはいつ頃でしたか？」
                  向く target: 「時期」「きっかけのタイミング」など
  - 仮定法     : もし○○が違っていたら / 仮に△△だったら結果は変わっていたか
                  例「もし残業が改善されていたら、結果は変わっていたと思いますか？」
                  向く target: 「改善されていれば結果が変わった可能性」など
  - 比較       : 他の部署/時期/職場と比べてどうだったか
                  例「以前の部署と比べて、特に違いを感じた部分はありましたか？」
                  向く target: 「期待と現実の差」「他事例との対比」など
  - 影響       : それによってどんな影響/変化が日常や仕事に生じたか
                  例「その状況は普段の働き方にどのような影響を与えましたか？」
                  向く target: 「ギャップが業務に与えた影響」など
  - 感情       : その時どんな気持ち/印象だったか
                  例「そのとき率直にどんな気持ちでしたか？」
                  向く target: 「決断時の感情」「印象」など
  - 具体エピソード : 具体的な場面/事例（連続使用は避ける）
                  例「具体的な場面があれば教えていただけますか？」
                  向く target: ほとんど何にでも使えるが多用注意。回答が抽象的すぎる時に使う

直近の AI 発話で既に使った視点は **可能な限り** 避けてください。ただし target に合う視点が
直前と同じになる場合は、視点重複より「未取得 target を埋める」を優先します。

reason には「選んだ視点で、どの未取得 target を埋めるために何を聞くか」を簡潔に書いてください。

═══ coverage_update の出力 ═══
直前のユーザー応答で **新しく取得できた target** があれば、coverage_update にその target 名と
1行要約 (40文字以内) を入れてください。既に取得済の target は再度入れる必要はありません。
新たな情報がなければ空オブジェクト {{}} を返してください。

target 名は上記「現ステップの取得項目」で示された名前と **完全一致** させてください。
（部分一致や言い換えは認識できません）

以下の JSON 形式で回答してください（JSON のみ、他のテキストは不要）:

ADVANCE の場合:
{{"decision": "ADVANCE", "coverage_update": {{"target名": "1行要約"}} }}

STAY の場合（angle と reason を必ず含めてください）:
{{"decision": "STAY", "angle": "時系列|仮定法|比較|影響|感情|具体エピソード のいずれか1つ", "reason": "未取得 target を埋めるために何を聞くか。1点に絞る。", "coverage_update": {{"target名": "1行要約"}} }}

CLARIFY の場合（ユーザーの聞き返し意図を短く要約してください）:
{{"decision": "CLARIFY", "reason": "ユーザーが何を確認したいか", "coverage_update": {{}} }}

PROCESS の場合（プロセスに関する質問意図を短く要約してください）:
{{"decision": "PROCESS", "reason": "ユーザーのプロセス質問の要点", "coverage_update": {{}} }}
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


def _format_targets_and_coverage(
    targets: list[str],
    step_coverage: dict[str, str],
) -> str:
    """ステップの target と取得状況を判定 LLM 向けに整形する."""
    if not targets:
        return "（このステップには取得項目が設定されていません）"
    lines = []
    for target in targets:
        summary = step_coverage.get(target, "").strip()
        if summary:
            lines.append(f"  ✅ {target}: {summary}")
        else:
            lines.append(f"  ❌ {target}: （未取得）")
    return "\n".join(lines)


async def should_advance(
    state: InterviewState,
    step_definitions: dict[int, dict[str, str]],
    max_step: int = 6,
) -> tuple[Literal["advance", "stay", "clarify", "process"], str, dict[str, str]]:
    """現在のステップから次に進むべきか LLM + ルールで判定する.

    Args:
        state: 現在の面談ステート.
        step_definitions: ステップ定義（name, purpose, instruction, targets を含む dict）.
        max_step: closing ステップ番号.

    Returns:
        (decision, reason, coverage_update):
            decision は "advance"/"stay"/"clarify"/"process",
            reason は STAY 時の深掘り理由（ADVANCE 時は空文字列）,
            coverage_update は直前ユーザー応答で新たに埋まった target → 要約のマップ。
    """
    current_step = state["current_step"]
    deep_dive_count = state["deep_dive_count"]
    non_answer_count = state.get("non_answer_count", 0)

    if non_answer_count >= MAX_NON_ANSWER:
        logger.info(
            f"Step {current_step}: 非回答反応上限 ({MAX_NON_ANSWER}) 到達 → STAY で本題に戻す"
        )
        return (
            "stay",
            "本題の回答がまだ不足しています。現在の質問に1点だけ具体的に答えてもらってください。",
            {},
        )

    if deep_dive_count >= MAX_DEEP_DIVE:
        logger.debug(f"Step {current_step}: 深掘り上限 ({MAX_DEEP_DIVE}) 到達 → 強制遷移")
        return ("advance", "", {})

    if current_step == 0:
        return ("advance", "", {})

    if current_step >= max_step:
        return ("advance", "", {})

    # ── コンテキスト構築 ──
    step_def = step_definitions.get(current_step, {})
    step_purpose = step_def.get("purpose", "")
    step_name = step_def.get("name", f"step_{current_step}")
    step_instruction = step_def.get("instruction", "")
    step_targets: list[str] = list(step_def.get("targets", []) or [])

    coverage_all = state.get("coverage", {}) or {}
    step_coverage: dict[str, str] = dict(coverage_all.get(current_step, {}) or {})
    targets_and_coverage = _format_targets_and_coverage(step_targets, step_coverage)

    # 前回の深掘り理由（視点情報を含む）
    prev_reason = state.get("deep_dive_reason", "")
    if deep_dive_count > 0 and prev_reason:
        prev_deep_dive_info = (
            f"前回の深掘り理由: {prev_reason}\n"
            "（同じ視点の連続使用は単調になるので、target に合うなら別視点を優先。"
            "ただし target との適合を優先し、無理に視点を変えないこと）"
        )
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
        targets_and_coverage=targets_and_coverage,
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
    logger.debug(f"Step {current_step}: LLM 応答: {raw[:300]}")

    # JSON パース（フォールバック付き）
    angle = ""
    coverage_update: dict[str, str] = {}
    try:
        parsed = json.loads(raw)
        decision = parsed.get("decision", "ADVANCE").upper()
        reason = parsed.get("reason", "")
        angle = parsed.get("angle", "") or ""
        cu = parsed.get("coverage_update", {}) or {}
        if isinstance(cu, dict):
            # 既知の target 名のみ採用（LLM が勝手に新しい target を作るのを防ぐ）
            valid = set(step_targets)
            for k, v in cu.items():
                if k in valid and isinstance(v, str) and v.strip():
                    coverage_update[k] = v.strip()
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

    if coverage_update:
        logger.info(
            f"Step {current_step}: coverage_update={coverage_update}"
        )

    if decision == "ADVANCE":
        logger.debug(f"Step {current_step}: LLM 判定 → ADVANCE")
        return ("advance", "", coverage_update)

    if decision == "CLARIFY":
        logger.info(f"Step {current_step}: LLM 判定 → CLARIFY")
        return ("clarify", reason, coverage_update)

    if decision == "PROCESS":
        logger.info(f"Step {current_step}: LLM 判定 → PROCESS")
        return ("process", reason, coverage_update)

    # STAY: angle を reason に前置して injection / ログで可視化する
    if angle:
        reason = f"視点「{angle}」で深掘り。{reason}"
    logger.info(f"Step {current_step}: LLM 判定 → STAY (deep_dive {deep_dive_count + 1}, reason={reason})")
    return ("stay", reason, coverage_update)


def make_route_after_evaluate(max_step: int = 6):
    """max_step を指定可能な route_after_evaluate ファクトリ."""

    def _route(state: InterviewState) -> str:
        if state["current_step"] >= max_step:
            return "closing"
        return "ask"

    return _route
