"""InterviewSession: 1 WebSocket 接続に対応する面談セッション.

LangGraph の StateGraph + MemorySaver でステップ遷移を管理する。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from interview_graph import (
    InterviewState,
    build_exit_interview_graph,
    build_compliance_graph,
    build_test_graph,
    EXIT_INTERVIEW_STEPS,
    COMPLIANCE_STEPS,
    TEST_STEPS,
)
from prompt_service import StepDef

logger = logging.getLogger(__name__)


class InterviewSession:
    """1 つの WebSocket 接続に対応する面談セッション.

    LangGraph の StateGraph + MemorySaver でステップ遷移を管理する。
    ask ノードの interrupt_after でグラフを一時停止し、
    新しいメッセージが入ったら resume して遷移判定を行う。
    """

    def __init__(
        self,
        scenario: str,
        custom_steps: dict[int, StepDef] | None = None,
    ):
        self.scenario = scenario
        self._lock = asyncio.Lock()

        if scenario == "compliance":
            graph_builder = build_compliance_graph()
            default_steps = COMPLIANCE_STEPS
        elif scenario == "test":
            graph_builder = build_test_graph()
            default_steps = TEST_STEPS
        else:
            graph_builder = build_exit_interview_graph()
            default_steps = EXIT_INTERVIEW_STEPS

        # custom_steps が渡された場合はそちらを優先
        self.step_definitions = custom_steps if custom_steps else default_steps

        memory = MemorySaver()
        self.graph = graph_builder.compile(
            checkpointer=memory,
            interrupt_after=["ask"],
        )
        self.config = {"configurable": {"thread_id": "1"}}

    def get_step_instruction(self, step: int) -> str:
        """指定ステップの instruction テキストを返す."""
        step_def = self.step_definitions.get(step, {})
        return step_def.get("instruction", "")

    async def initialize(self):
        """グラフを初期状態で起動し step_0 で待機する."""
        initial_state: InterviewState = {
            "current_step": 0,
            "messages": [],
            "deep_dive_count": 0,
            "deep_dive_reason": "",
            "step_summaries": {},
            "is_complete": False,
        }
        await self.graph.ainvoke(initial_state, self.config)
        logger.info("InterviewSession graph initialized at step_0")

    async def process_user_message(self, text: str) -> dict:
        """ユーザー発話を会話履歴に追加する（遷移判定は行わない）."""
        self.graph.update_state(
            self.config,
            {"messages": [HumanMessage(content=text)]},
        )
        logger.info(f"Appended user message to graph state: {text[:80]}")
        return self.get_status()

    async def process_ai_message(self, text: str) -> dict:
        """AI 発話を会話履歴に追加し、グラフを resume してステップ遷移を判定する.

        返却 dict に遷移情報を追加:
        - transition: "advance" | "stay" | "none"
        - step_instruction: ADVANCE 時は新ステップの instruction
        - deep_dive_reason: STAY 時の深掘り理由
        """
        async with self._lock:
            self.graph.update_state(
                self.config,
                {"messages": [AIMessage(content=text)]},
            )
            logger.info(f"Appended AI message to graph state: {text[:80]}")

            snapshot = self.graph.get_state(self.config)
            if snapshot.values.get("is_complete"):
                status = self.get_status()
                status["transition"] = "none"
                return status

            old_step = snapshot.values.get("current_step", 0)
            old_deep_dive = snapshot.values.get("deep_dive_count", 0)

            await self.graph.ainvoke(None, self.config)

            status = self.get_status()
            new_step = status["current_step"]
            new_deep_dive = status["deep_dive_count"]

            if new_step > old_step:
                status["transition"] = "advance"
                status["step_instruction"] = self.get_step_instruction(new_step)
                logger.info(f"Transition: ADVANCE step {old_step} → {new_step}")
            elif new_deep_dive > old_deep_dive:
                status["transition"] = "stay"
                logger.info(
                    f"Transition: STAY on step {new_step} (deep_dive={new_deep_dive})"
                )
            else:
                status["transition"] = "none"

            return status

    def get_status(self) -> dict:
        """デバッグ用のステータス情報を返す."""
        snapshot = self.graph.get_state(self.config)
        values = snapshot.values
        return {
            "type": "interview.state",
            "current_step": values.get("current_step", 0),
            "deep_dive_count": values.get("deep_dive_count", 0),
            "deep_dive_reason": values.get("deep_dive_reason", ""),
            "is_complete": values.get("is_complete", False),
            "step_summaries": values.get("step_summaries", {}),
        }
