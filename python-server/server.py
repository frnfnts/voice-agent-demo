import asyncio
import json
import logging
import os
import copy
from dotenv import load_dotenv
import websockets
from websockets.legacy.client import connect
from aiohttp import web, WSMsgType
from google_drive_docs_export import export_doc
from pathlib import Path
from collections import Counter

from langchain_core.messages import AIMessage, HumanMessage
from interview_graph import (
    InterviewState,
    EXIT_INTERVIEW_STEPS,
    COMPLIANCE_STEPS,
    should_advance,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()
PORT = int(os.getenv("PORT", 3000))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOG_LEVEL = os.getenv("LOG_LEVEL", "debug").lower()  # set LOG_LEVEL=info to silence debug logs
DEBUG_ENABLED = LOG_LEVEL == "debug"
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "*")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Content-Type, Authorization, ngrok-skip-browser-warning")
INSTRUCTION_DOC_ID = os.getenv("INSTRUCTION_DOC_ID", "1cQSHjpoijqEkbvU8h5ZlMzk3qIdy6u4gjL4qXM4BA9w")
COMPLIANCE_INSTRUCTION_DOC_ID = os.getenv("COMPLIANCE_INSTRUCTION_DOC_ID", "17X_7fQzE14K6FFYWj9PQTPFLCHzsfVG8-phoPH-f37g")

logger.setLevel("DEBUG" if DEBUG_ENABLED else "INFO")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY must be set in .env file")


def debug_log_event(message: str, event: dict):
    """デバッグ用にイベントをJSONで出力する。audioとinstructionsは100文字に短縮。"""
    if not DEBUG_ENABLED:
        return

    truncated = copy.deepcopy(event)

    # audio と session.instructions の場合は100文字に切り詰める
    if "audio" in truncated and isinstance(truncated["audio"], str) and len(truncated["audio"]) > 100:
        truncated["audio"] = truncated["audio"][:100] + "..."
        if Counter(truncated["audio"])["A"] > 80:
            return
    if "session" in truncated and isinstance(truncated.get("session"), dict):
        if "instructions" in truncated["session"] and isinstance(truncated["session"]["instructions"], str) and len(truncated["session"]["instructions"]) > 100:
            truncated["session"]["instructions"] = truncated["session"]["instructions"][:100] + "..."
    if "delta" in truncated and isinstance(truncated.get("delta"), str) and len(truncated["delta"]) > 100:
        truncated["delta"] = truncated["delta"][:100] + "..."
    if event.get("type") == "response.audio_transcript.delta":
        return

    logger.debug(f"{message}: {json.dumps(truncated, indent=2, ensure_ascii=False)}")


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    origin = request.headers.get("Origin")
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = "3600"
    return response


async def connect_to_openai():
    """Connect to OpenAI's WebSocket endpoint."""
    uri = "wss://api.openai.com/v1/realtime?model=gpt-realtime-mini-2025-12-15"

    try:
        ws = await connect(
            uri,
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
                "OpenAI-Beta": "realtime=v1",
            },
            subprotocols=["realtime"],
        )
        logger.info("Successfully connected to OpenAI")

        response = await ws.recv()
        try:
            event = json.loads(response)
            if event.get("type") != "session.created":
                logger.error(f"Unexpected event type: {event}")
                raise Exception(f"Expected session.created, got {event.get('type')}")
            logger.info("Received session.created response")

            update_session = {
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "modalities": ["text", "audio"],
                    "voice": "alloy",
                    "input_audio_transcription": {
                        "model": "gpt-4o-mini-transcribe",
                    },
                },
            }
            await ws.send(json.dumps(update_session))
            logger.info("Sent session.create message")

            return (
                ws,
                event,
            )
        except json.JSONDecodeError:
            raise Exception(f"Invalid JSON response from OpenAI: {response}")

    except Exception as e:
        logger.error(f"Failed to connect to OpenAI: {str(e)}")
        raise


# ステップ数の上限（closing ステップ）
_MAX_STEP = 6


class InterviewSession:
    """1 つの WebSocket 接続に対応する面談セッション."""

    def __init__(self, scenario: str, full_prompt: str):
        self.scenario = scenario
        self.full_prompt = full_prompt

        steps = COMPLIANCE_STEPS if scenario == "compliance" else EXIT_INTERVIEW_STEPS
        self.step_purposes: dict[int, str] = {
            k: v["purpose"] for k, v in steps.items()
        }

        self.state: InterviewState = {
            "current_step": 0,
            "messages": [],
            "deep_dive_count": 0,
            "step_summaries": {},
            "is_complete": False,
        }

    async def process_user_message(self, text: str) -> dict:
        """ユーザー発話を会話履歴に追加する（遷移判定は行わない）."""
        self.state["messages"].append(HumanMessage(content=text))
        return self.get_status()

    async def process_ai_message(self, text: str) -> dict:
        """AI 発話を会話履歴に追加し、ステップ遷移を判定する."""
        self.state["messages"].append(AIMessage(content=text))

        if self.state["is_complete"]:
            return self.get_status()

        decision = await should_advance(self.state, self.step_purposes)

        if decision == "advance":
            next_step = self.state["current_step"] + 1
            self.state["current_step"] = min(next_step, _MAX_STEP)
            self.state["deep_dive_count"] = 0
            if self.state["current_step"] >= _MAX_STEP:
                self.state["is_complete"] = True
        else:
            self.state["deep_dive_count"] += 1

        return self.get_status()

    def get_status(self) -> dict:
        """デバッグ用のステータス情報を返す."""
        return {
            "type": "interview.state",
            "current_step": self.state["current_step"],
            "deep_dive_count": self.state["deep_dive_count"],
            "is_complete": self.state["is_complete"],
            "step_summaries": self.state.get("step_summaries", {}),
        }


async def _process_ai_transcript(
    session: InterviewSession,
    transcript: str,
    ws: web.WebSocketResponse,
    is_debug: bool,
) -> None:
    """AI トランスクリプトをバックグラウンドで処理する.

    グラフ実行中に例外が発生してもリレーループを巻き込まないよう
    ここで例外をキャッチしてログに残す。
    """
    try:
        status = await session.process_ai_message(transcript)
        logger.info(
            f"Interview state (AI): step={status['current_step']}, done={status['is_complete']}"
        )
        if is_debug and not ws.closed:
            await ws.send_str(json.dumps(status, ensure_ascii=False))
    except Exception:
        logger.exception("Error processing AI transcript in graph")


class WebSocketRelay:
    def __init__(self):
        """Initialize the WebSocket relay server."""
        self.connections = {}
        self.sessions: dict[web.WebSocketResponse, InterviewSession] = {}

    async def handle_browser_connection(self, request: web.Request):
        """Handle a WebSocket connection from the browser."""
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return web.Response(text="OK")

        ws = web.WebSocketResponse(protocols=("realtime",))
        await ws.prepare(request)

        # scenario と is_debug は session.update の instructions (JSON) から取得する
        scenario = "exit_interview"
        is_debug = False
        session: InterviewSession | None = None

        logger.info(f"Browser connected from {request.remote}")
        openai_ws = None

        try:
            # Connect to OpenAI
            openai_ws, session_created = await connect_to_openai()
            self.connections[ws] = openai_ws

            logger.info("Connected to OpenAI successfully!")

            await ws.send_str(json.dumps(session_created))
            logger.info("Forwarded session.created to browser")

            async def handle_browser_messages():
                nonlocal session, scenario, is_debug
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        message = msg.data
                        try:
                            event = json.loads(message)
                            logger.info(f'Relaying "{event.get("type")}" to OpenAI')
                            debug_log_event("Browser -> OpenAI", event)

                            # session.update にプロンプトが含まれる場合、セッション初期化
                            if (
                                event.get("type") == "session.update"
                                and event.get("session", {}).get("instructions")
                                and session is None
                            ):
                                raw = event["session"]["instructions"]
                                try:
                                    payload = json.loads(raw)
                                    full_prompt = payload.get("instruction", raw)
                                    scenario = payload.get("scenario", scenario)
                                    is_debug = bool(payload.get("is_debug", is_debug))
                                except (json.JSONDecodeError, TypeError):
                                    full_prompt = raw

                                # OpenAI に送る instructions は instruction テキストのみにする
                                event["session"]["instructions"] = full_prompt
                                message = json.dumps(event)

                                session = InterviewSession(scenario, full_prompt)
                                self.sessions[ws] = session
                                logger.info(f"InterviewSession created for scenario={scenario}, debug={is_debug}")
                                if is_debug:
                                    await ws.send_str(json.dumps(session.get_status()))

                            await openai_ws.send(message)

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
                            logger.info(
                                f'Relaying "{event.get("type")}" from OpenAI'
                            )
                            debug_log_event("OpenAI -> Browser", event)
                            await ws.send_str(message)

                            # ユーザーの音声入力トランスクリプト → 会話履歴に追加
                            if (
                                session
                                and event.get("type") == "conversation.item.input_audio_transcription.completed"
                            ):
                                transcript = event.get("transcript", "").strip()
                                if transcript:
                                    logger.info(f"User transcript: {transcript[:80]}")
                                    try:
                                        status = await session.process_user_message(transcript)
                                        logger.info(f"Interview state (user): step={status['current_step']}")
                                        if is_debug:
                                            await ws.send_str(json.dumps(status, ensure_ascii=False))
                                    except Exception:
                                        logger.exception("Error processing user transcript")

                            # AI の音声応答トランスクリプト → 会話履歴に追加 + ステップ遷移判定
                            # グラフ実行は重い（複数 LLM 呼び出し）ためバックグラウンドで実行し
                            # リレーループをブロックしない
                            if (
                                session
                                and event.get("type") == "response.audio_transcript.done"
                            ):
                                transcript = event.get("transcript", "").strip()
                                if transcript:
                                    logger.info(f"AI transcript: {transcript[:80]}")
                                    asyncio.create_task(
                                        _process_ai_transcript(
                                            session, transcript, ws, is_debug
                                        )
                                    )

                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from OpenAI: {message}")
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(
                        f"OpenAI connection closed normally: code={e.code}, reason={e.reason}"
                    )
                    raise

            try:
                await asyncio.gather(
                    handle_browser_messages(), handle_openai_messages()
                )
            except websockets.exceptions.ConnectionClosed:
                logger.info("One of the connections closed, cleaning up")

        except Exception as e:
            logger.error(f"Error handling connection: {str(e)}")
        finally:
            if ws in self.sessions:
                del self.sessions[ws]
            if ws in self.connections:
                if openai_ws and not openai_ws.closed:
                    await openai_ws.close(1000, "Normal closure")
                del self.connections[ws]
            if not ws.closed:
                await ws.close(code=1000, message=b"Normal closure")

        return ws

    SCENARIO_INSTRUCTION_DOC_ID = {
        "exit_interview": INSTRUCTION_DOC_ID,
        "compliance": COMPLIANCE_INSTRUCTION_DOC_ID,
    }

    async def handle_get_instruction(self, request: web.Request):
        scenario = request.query.get("scenario", "exit_interview")
        wd = Path(__file__).parent
        service_account_path = wd / "ame-ai-agent.json"

        doc_id = self.SCENARIO_INSTRUCTION_DOC_ID.get(scenario)
        if not doc_id:
            return web.Response(status=400, text="Invalid scenario")
        with open(service_account_path, "r", encoding="utf-8") as f:
            sa_info = json.load(f)
            content = export_doc(doc_id, sa_info, "text/plain")
        return web.Response(text=content.decode("utf-8"))

    async def serve(self):
        """Start the WebSocket relay server with HTTP endpoints."""
        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get("/", self.handle_browser_connection)
        app.router.add_get("/get-instruction", self.handle_get_instruction)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        logger.info(f"Server started on http://0.0.0.0:{PORT} and ws://0.0.0.0:{PORT}")
        await asyncio.Future()


def main():
    """Main entry point for the WebSocket relay server."""
    relay = WebSocketRelay()
    try:
        asyncio.run(relay.serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    finally:
        logger.info("Server shutdown complete")


if __name__ == "__main__":
    main()
