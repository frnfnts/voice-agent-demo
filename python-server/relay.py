"""WebSocket 双方向リレー: ブラウザ ↔ OpenAI Realtime API.

インタビューロジックはコールバック関数で注入する。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections import Counter
from typing import Callable, Awaitable

import websockets
from aiohttp import web, WSMsgType
from websockets.legacy.client import connect

from config import OPENAI_API_KEY, MODEL_REALTIME, MODEL_TRANSCRIBE, DEBUG_ENABLED

logger = logging.getLogger(__name__)
logger.setLevel("INFO")


# ── 型エイリアス ──

# session.update を受信した時のコールバック
# (event, ws, openai_ws) -> (modified_message, post_send_instruction | None)
OnSessionUpdate = Callable[
    [dict, web.WebSocketResponse, any],
    Awaitable[tuple[str, str | None]],
]

# ユーザートランスクリプトを受信した時のコールバック
OnUserTranscript = Callable[
    [str, web.WebSocketResponse, any],
    Awaitable[None],
]

# AI トランスクリプトを受信した時のコールバック
OnAITranscript = Callable[
    [str, web.WebSocketResponse, any],
    Awaitable[None],
]


def debug_log_event(message: str, event: dict) -> None:
    """デバッグ用にイベントをJSONで出力する。audioとinstructionsは100文字に短縮。"""
    if not DEBUG_ENABLED:
        return

    truncated = copy.deepcopy(event)

    if "audio" in truncated and isinstance(truncated["audio"], str) and len(truncated["audio"]) > 100:
        truncated["audio"] = truncated["audio"][:100] + "..."
        if Counter(truncated["audio"])["A"] > 80:
            return
    if "session" in truncated and isinstance(truncated.get("session"), dict):
        inst = truncated["session"].get("instructions")
        if isinstance(inst, str) and len(inst) > 100:
            truncated["session"]["instructions"] = inst[:100] + "..."
    if "delta" in truncated and isinstance(truncated.get("delta"), str) and len(truncated["delta"]) > 100:
        truncated["delta"] = truncated["delta"][:100] + "..."
    if event.get("type") == "response.audio_transcript.delta":
        return

    logger.debug(f"{message}: {json.dumps(truncated, indent=2, ensure_ascii=False)}")


async def connect_to_openai():
    """OpenAI Realtime API に WebSocket 接続する."""
    uri = f"wss://api.openai.com/v1/realtime?model={MODEL_REALTIME}"

    ws = await connect(
        uri,
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        subprotocols=["realtime"],
    )
    logger.debug("Successfully connected to OpenAI")

    response = await ws.recv()
    event = json.loads(response)
    if event.get("type") != "session.created":
        raise Exception(f"Expected session.created, got {event.get('type')}. event: {event}")
    logger.debug("Received session.created response")

    update_session = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "modalities": ["text", "audio"],
            "voice": "marin",
            "speed": 0.9,
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.7,
                "silence_duration_ms": 1200,
                "prefix_padding_ms": 500,
                # 次の会話を開始せず、 evaluate のみをトリガーする設定
                "create_response": False,
            },
            "input_audio_transcription": {
                "model": MODEL_TRANSCRIBE,
            },
        },
    }
    await ws.send(json.dumps(update_session))
    logger.debug("Sent session.update message")

    return ws, event


async def inject_system_message(openai_ws, text: str) -> None:
    """OpenAI Realtime API に conversation.item.create でシステムメッセージを注入する."""
    event = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "system",
            "content": [{"type": "input_text", "text": text}],
        },
    }
    await openai_ws.send(json.dumps(event))
    logger.info(f"Injected system message to OpenAI: {text}")


async def send_response_create(openai_ws) -> None:
    """OpenAI Realtime API に response.create を送信して AI 応答を手動トリガーする."""
    event = {"type": "response.create"}
    await openai_ws.send(json.dumps(event))
    logger.info("Sent response.create to OpenAI")


async def run_relay(
    ws: web.WebSocketResponse,
    on_session_update: OnSessionUpdate | None = None,
    on_user_transcript: OnUserTranscript | None = None,
    on_ai_transcript: OnAITranscript | None = None,
) -> None:
    """ブラウザ ↔ OpenAI の双方向 WebSocket リレーを実行する.

    Args:
        ws: ブラウザ側の WebSocket.
        on_session_update: session.update 受信時のコールバック.
        on_user_transcript: ユーザートランスクリプト受信時のコールバック.
        on_ai_transcript: AI トランスクリプト受信時のコールバック.
    """
    openai_ws, session_created = await connect_to_openai()

    await ws.send_str(json.dumps(session_created))
    logger.debug("Forwarded session.created to browser")

    async def handle_browser_messages():
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                message = msg.data
                try:
                    event = json.loads(message)
                    event_type = event.get("type")
                    logger.debug(f'Relaying "{event_type}" to OpenAI')
                    debug_log_event("Browser -> OpenAI", event)

                    # ブラウザからの response.create はサーバーが一元管理するためドロップ
                    if event_type == "response.create":
                        logger.debug("Dropped response.create from browser (server-controlled)")
                        continue

                    # session.update のインターセプト
                    post_send_instruction = None
                    if (
                        on_session_update
                        and event_type == "session.update"
                        and event.get("session", {}).get("instructions")
                    ):
                        message, post_send_instruction = await on_session_update(event, ws, openai_ws)

                    await openai_ws.send(message)

                    # session.update 送信後に greeting instruction を注入
                    if post_send_instruction:
                        await inject_system_message(openai_ws, post_send_instruction)
                        await send_response_create(openai_ws)

                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from browser: {message}")
            elif msg.type == WSMsgType.ERROR:
                raise ws.exception()

    async def handle_openai_messages():
        try:
            while True:
                message = await openai_ws.recv()
                try:
                    event = json.loads(message)
                    event_type = event.get("type", "unknown")
                    logger.debug(f'Relaying "{event_type}" from OpenAI')
                    debug_log_event("OpenAI -> Browser", event)
                    if event_type in ("error", "session.updated", "response.created", "response.done", "response.audio_transcript.done"):
                        logger.info(f'OpenAI event: {event_type} | {json.dumps({k: v for k, v in event.items() if k not in ("delta", "audio")}, ensure_ascii=False)[:200]}')
                    await ws.send_str(message)

                    # ユーザートランスクリプト
                    if (
                        on_user_transcript
                        and event.get("type") == "conversation.item.input_audio_transcription.completed"
                    ):
                        transcript = event.get("transcript", "").strip()
                        if transcript:
                            try:
                                await on_user_transcript(transcript, ws, openai_ws)
                            except Exception:
                                logger.exception("Error processing user transcript")

                    # AI トランスクリプト
                    if (
                        on_ai_transcript
                        and event.get("type") == "response.audio_transcript.done"
                    ):
                        transcript = event.get("transcript", "").strip()
                        if transcript:
                            try:
                                await on_ai_transcript(transcript, ws, openai_ws)
                            except Exception:
                                logger.exception("Error processing AI transcript")

                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from OpenAI: {message}")
        except websockets.exceptions.ConnectionClosed as e:
            logger.debug(
                f"OpenAI connection closed normally: code={e.code}, reason={e.reason}"
            )
            raise

    try:
        await asyncio.gather(handle_browser_messages(), handle_openai_messages())
    except websockets.exceptions.ConnectionClosed:
        logger.debug("One of the connections closed, cleaning up")
    finally:
        if openai_ws and not openai_ws.closed:
            await openai_ws.close(1000, "Normal closure")
        if not ws.closed:
            await ws.close(code=1000, message=b"Normal closure")
