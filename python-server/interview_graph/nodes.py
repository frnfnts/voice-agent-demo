"""面談グラフのノード関数.

ノードは 4 種類:
  greet    — 面談開始・挨拶
  ask      — AI が質問する (interrupt ポイント)
  evaluate — 深掘りすべきか判定し state を更新
  closing  — 面談終了
"""

from __future__ import annotations

import logging
from typing import Any

from .state import InterviewState

logger = logging.getLogger(__name__)


# ── greet / closing ──────────────────────────────────────

def make_greeting_node():
    """Step 0 (挨拶・趣旨説明) ノード."""

    async def greeting_node(state: InterviewState) -> dict[str, Any]:
        logger.debug("greet: entered")
        return {
            "current_step": 0,
            "deep_dive_count": 0,
            "is_complete": False,
        }

    return greeting_node


def make_closing_node(closing_step: int = 6):
    """面談終了ノード. is_complete=True にする."""

    async def closing_node(state: InterviewState) -> dict[str, Any]:
        logger.debug("closing: interview complete")
        return {
            "current_step": closing_step,
            "deep_dive_count": 0,
            "is_complete": True,
        }

    return closing_node


# ── ask / listen / evaluate ──────────────────────────────

async def ask_node(state: InterviewState) -> dict[str, Any]:
    """AI が質問するノード.

    interrupt_after でここで一時停止し、
    ユーザーが返答した後 AI が応答したタイミングで resume → evaluate へ遷移する。
    """
    logger.debug(f"ask: step {state['current_step']} — waiting for conversation")
    return {}


def make_evaluate_node(step_definitions: dict[int, dict[str, str]], max_step: int = 6):
    """深掘りすべきか判定するノードのファクトリ.

    should_advance() を呼び、STAY なら deep_dive_count++,
    ADVANCE なら current_step++ して返す。
    """
    from .edges import should_advance

    async def evaluate_node(state: InterviewState) -> dict[str, Any]:
        decision, reason = await should_advance(state, step_definitions, max_step)
        current_step = state["current_step"]

        if decision == "stay":
            new_count = state["deep_dive_count"] + 1
            logger.debug(f"evaluate: STAY on step {current_step} (deep_dive={new_count}, reason={reason})")
            return {"deep_dive_count": new_count, "deep_dive_reason": reason}
        else:
            next_step = current_step + 1
            logger.debug(f"evaluate: ADVANCE step {current_step} → {next_step}")
            return {"current_step": next_step, "deep_dive_count": 0, "deep_dive_reason": ""}

    return evaluate_node
