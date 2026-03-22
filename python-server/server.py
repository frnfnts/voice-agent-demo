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

LOG_FMT = "%(asctime)s - %(levelname)s - %(message)s"

# stdout ハンドラ
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(LOG_FMT))

# ファイルハンドラ (python-server/server.log)
_log_path = Path(__file__).parent / "server.log"
_file_handler = logging.FileHandler(_log_path, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(LOG_FMT))

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])
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

# logger.setLevel("DEBUG" if DEBUG_ENABLED else "INFO")
logger.setLevel("INFO")

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
        logger.debug("Successfully connected to OpenAI")

        response = await ws.recv()
        try:
            event = json.loads(response)
            if event.get("type") != "session.created":
                logger.error(f"Unexpected event type: {event}")
                raise Exception(f"Expected session.created, got {event.get('type')}")
            logger.debug("Received session.created response")

            update_session = {
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "modalities": ["text", "audio"],
                    "voice": "marin",
                    "speed": 0.9,
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.7,
                        "silence_duration_ms": 1200,
                        "prefix_padding_ms": 500,
                    },
                    "input_audio_transcription": {
                        "model": "gpt-4o-mini-transcribe",
                    },
                },
            }
            await ws.send(json.dumps(update_session))
            logger.debug("Sent session.create message")

            return (
                ws,
                event,
            )
        except json.JSONDecodeError:
            raise Exception(f"Invalid JSON response from OpenAI: {response}")

    except Exception as e:
        logger.error(f"Failed to connect to OpenAI: {str(e)}")
        raise


class InterviewSession:
    """1 つの WebSocket 接続に対応する面談セッション.

    LangGraph の StateGraph + MemorySaver でステップ遷移を管理する。
    ask ノードの interrupt_after でグラフを一時停止し、
    新しいメッセージが入ったら resume して遷移判定を行う。
    """

    def __init__(self, scenario: str):
        self.scenario = scenario
        self._lock = asyncio.Lock()

        if scenario == "compliance":
            graph_builder = build_compliance_graph()
            self.step_definitions = COMPLIANCE_STEPS
        elif scenario == "test":
            graph_builder = build_test_graph()
            self.step_definitions = TEST_STEPS
        else:
            graph_builder = build_exit_interview_graph()
            self.step_definitions = EXIT_INTERVIEW_STEPS

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
        """​AI 発話を会話履歴に追加し、グラフを resume してステップ遷移を判定する.

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

            # resume — conditional edge が should_advance を呼び、
            # STAY なら同じノード、ADVANCE なら次のノードへ進んで interrupt_after
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
                logger.info(f"Transition: STAY on step {new_step} (deep_dive={new_deep_dive})")
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


async def _process_ai_transcript(
    session: InterviewSession,
    transcript: str,
    ws: web.WebSocketResponse,
    openai_ws,
    is_debug: bool,
) -> None:
    """AI トランスクリプトをバックグラウンドで処理する.

    グラフ実行中に例外が発生してもリレーループを巻き込まないよう
    ここで例外をキャッチしてログに残す。
    state 遷移があれば OpenAI に system メッセージを注入する。
    """
    try:
        status = await session.process_ai_message(transcript)
        logger.info(
            f"Interview state (AI): step={status['current_step']}, "
            f"transition={status.get('transition')}, done={status['is_complete']}"
        )

        # ── state 変更時に OpenAI へシステムメッセージを注入 ──
        transition = status.get("transition", "none")
        if transition != "none" and openai_ws and not openai_ws.closed:
            if transition == "advance":
                instruction = status.get("step_instruction", "")
                if instruction:
                    await _inject_system_message(openai_ws, instruction)
            elif transition == "stay":
                reason = status.get("deep_dive_reason", "")
                if reason:
                    msg = f"以下の点についてもう少し深く聞いてください: {reason}"
                    await _inject_system_message(openai_ws, msg)

        if is_debug and not ws.closed:
            await ws.send_str(json.dumps(status, ensure_ascii=False))
    except Exception:
        logger.exception("Error processing AI transcript in graph")


async def _inject_system_message(openai_ws, text: str) -> None:
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
    logger.info(f"Injected system message to OpenAI: {text[:80]}")


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

            logger.debug("Connected to OpenAI successfully!")

            await ws.send_str(json.dumps(session_created))
            logger.debug("Forwarded session.created to browser")

            async def handle_browser_messages():
                nonlocal session, scenario, is_debug
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        message = msg.data
                        try:
                            event = json.loads(message)
                            logger.debug(f'Relaying "{event.get("type")}" to OpenAI')
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

                                session = InterviewSession(scenario)
                                await session.initialize()
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
                            logger.debug(
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
                                    logger.debug(f"User transcript: {transcript[:80]}")
                                    try:
                                        status = await session.process_user_message(transcript)
                                        logger.debug(f"Interview state (user): step={status['current_step']}")
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
                                    logger.debug(f"AI transcript: {transcript[:80]}")
                                    asyncio.create_task(_process_ai_transcript(session, transcript, ws, openai_ws, is_debug))
                                    # await _process_ai_transcript(session, transcript, ws, openai_ws, is_debug)

                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON from OpenAI: {message}")
                except websockets.exceptions.ConnectionClosed as e:
                    logger.debug(
                        f"OpenAI connection closed normally: code={e.code}, reason={e.reason}"
                    )
                    raise

            try:
                await asyncio.gather(
                    handle_browser_messages(), handle_openai_messages()
                )
            except websockets.exceptions.ConnectionClosed:
                logger.debug("One of the connections closed, cleaning up")

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

        doc_id = self.SCENARIO_INSTRUCTION_DOC_ID.get(scenario)
        if not doc_id:
            # Google Doc ID がないシナリオはローカルファイルから読み込む
            LOCAL_PROMPT_FILES = {"test": "prompt_test.txt"}
            local_file = LOCAL_PROMPT_FILES.get(scenario)
            if not local_file:
                return web.Response(status=400, text="Invalid scenario")
            prompt_path = wd / local_file
            if not prompt_path.exists():
                return web.Response(status=404, text=f"Prompt file not found: {local_file}")
            content = prompt_path.read_text(encoding="utf-8")
            return web.Response(text=content)

        service_account_path = wd / "ame-ai-agent.json"
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
