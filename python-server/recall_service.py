"""Recall.ai ボットライフサイクル管理."""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp as _aiohttp
from aiohttp import web

from config import RECALL_TOKEN, RECALL_API_BASE, DISCONNECT_DELAY

logger = logging.getLogger(__name__)


async def recall_leave_call(bot_id: str) -> None:
    """Recall.ai API を呼び出してボットをミーティングから退出させる."""
    if not RECALL_TOKEN:
        logger.warning("RECALL_TOKEN not set, skipping Recall.ai leave_call")
        return
    url = f"{RECALL_API_BASE}/bot/{bot_id}/leave_call/"
    headers = {
        "Authorization": RECALL_TOKEN,
        "accept": "application/json",
    }
    try:
        async with _aiohttp.ClientSession() as http_session:
            async with http_session.post(url, headers=headers) as resp:
                logger.info(f"Recall.ai leave_call response: {resp.status}")
    except Exception:
        logger.exception("Error calling Recall.ai leave_call")


async def schedule_disconnect(
    ws: web.WebSocketResponse,
    openai_ws,
    recall_bot_id: str | None = None,
) -> None:
    """面談完了後に遅延して WebSocket 接続を切断する."""
    await asyncio.sleep(DISCONNECT_DELAY)
    logger.info("Disconnecting after interview completion...")

    # ブラウザに面談完了イベントを送信
    if not ws.closed:
        try:
            await ws.send_str(json.dumps({"type": "interview.complete"}))
        except Exception:
            logger.exception("Error sending interview.complete to browser")

    # Recall.ai ボットを退出させる
    if recall_bot_id:
        await recall_leave_call(recall_bot_id)

    # OpenAI WebSocket を閉じる
    if openai_ws and not openai_ws.closed:
        try:
            await openai_ws.close(1000, "Interview complete")
        except Exception:
            logger.exception("Error closing OpenAI WebSocket")

    # ブラウザ WebSocket を閉じる
    if not ws.closed:
        try:
            await ws.close(code=1000, message=b"Interview complete")
        except Exception:
            logger.exception("Error closing browser WebSocket")

    logger.info("Disconnected after interview completion")
