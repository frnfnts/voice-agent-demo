"""Voice Agent Demo — WebSocket リレーサーバー.

ブラウザ ↔ OpenAI Realtime API の双方向リレーに加え、
LangGraph による面談ステップ管理を行うオーケストレーター。
"""

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from config import PORT, OPENAI_API_KEY, DEBUG_ENABLED, LOG_FILE_PATH, LOG_LEVEL
from interview_session import InterviewSession
from prompt_service import get_prompt
from recall_service import recall_leave_call, schedule_disconnect
from relay import run_relay, inject_system_message, send_response_create

# ── ロギング設定 ──

LOG_FMT = "%(asctime)s - %(levelname)s - %(message)s"
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(LOG_FMT))
_file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(LOG_FMT))
logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])

# プロジェクト内モジュールだけ LOG_LEVEL を適用
_APP_LOGGERS = [
    __name__,
    "relay",
    "interview_session",
    "interview_graph.nodes",
    "interview_graph.edges",
    "prompt_service",
    "recall_service",
]
for _name in _APP_LOGGERS:
    logging.getLogger(_name).setLevel(LOG_LEVEL)

logger = logging.getLogger(__name__)

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY must be set in .env file")


# ── CORS ミドルウェア ──

@web.middleware
async def cors_middleware(request: web.Request, handler):
    from config import CORS_ALLOW_ORIGIN, CORS_ALLOW_METHODS, CORS_ALLOW_HEADERS

    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = "3600"
    return response


class WebSocketRelay:
    def __init__(self):
        self.recall_bot_id: str | None = None

    # ── WebSocket 接続ハンドラ ──

    async def handle_browser_connection(self, request: web.Request):
        """ブラウザからの WebSocket 接続を処理する."""
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return web.Response(text="OK")

        ws = web.WebSocketResponse(protocols=("realtime",))
        await ws.prepare(request)
        logger.info(f"Browser connected from {request.remote}")

        # セッション状態 (クロージャで共有)
        session: InterviewSession | None = None
        scenario = "exit_interview"
        is_debug = False

        async def on_session_update(event, browser_ws, openai_ws):
            nonlocal session, scenario, is_debug

            if session is not None:
                return json.dumps(event), None

            raw = event["session"]["instructions"]
            try:
                payload = json.loads(raw)
                full_prompt = payload.get("instruction", raw)
                scenario = payload.get("scenario", scenario)
                is_debug = bool(payload.get("is_debug", is_debug))
            except (json.JSONDecodeError, TypeError):
                full_prompt = raw

            event["session"]["instructions"] = full_prompt
            message = json.dumps(event)

            # プロンプトからステップ定義を取得
            prompt_data = get_prompt(scenario)
            if not prompt_data.steps:
                logger.error(f"No step definitions found for scenario={scenario}")
                return message, True

            session = InterviewSession(prompt_data.steps)
            await session.initialize()
            logger.info(f"InterviewSession created for scenario={scenario}, steps={list(prompt_data.steps.keys())}, debug={is_debug}")

            if is_debug:
                await browser_ws.send_str(json.dumps(session.get_status()))

            # Step 0 (greeting) の instruction を返す → relay が session.update 送信後に注入
            greeting_instruction = session.get_step_instruction(0)
            return message, greeting_instruction

        async def on_user_transcript(transcript, browser_ws, openai_ws):
            if not session:
                return
            logger.debug(f"User transcript: {transcript[:80]}")
            status = await session.process_user_message(transcript)
            logger.debug(
                f"Interview state (user): step={status['current_step']}, "
                f"transition={status.get('transition')}, done={status['is_complete']}"
            )

            # 遷移に応じて OpenAI へシステムメッセージを注入 + 手動応答トリガー
            transition = status.get("transition", "none")
            if openai_ws and not openai_ws.closed:
                if transition == "advance":
                    instruction = status.get("step_instruction", "")
                    if instruction:
                        await inject_system_message(openai_ws, instruction)
                elif transition == "stay":
                    reason = status.get("deep_dive_reason", "")
                    if reason:
                        msg = f"以下の点についてもう少し深く聞いてください: {reason}"
                        await inject_system_message(openai_ws, msg)
                # evaluate 完了後に AI 応答を手動トリガー
                await send_response_create(openai_ws)

            if is_debug and not browser_ws.closed:
                await browser_ws.send_str(json.dumps(status, ensure_ascii=False))

            # 面談完了時に切断をスケジュール
            if status.get("is_complete"):
                asyncio.create_task(
                    schedule_disconnect(browser_ws, openai_ws, self.recall_bot_id)
                )

        async def on_ai_transcript(transcript, browser_ws, openai_ws):
            if not session:
                return
            logger.debug(f"AI transcript: {transcript[:80]}")
            try:
                status = await session.process_ai_message(transcript)
                logger.debug(
                    f"Interview state (AI): step={status['current_step']}, done={status['is_complete']}"
                )
                if is_debug and not browser_ws.closed:
                    await browser_ws.send_str(json.dumps(status, ensure_ascii=False))
            except Exception:
                logger.exception("Error processing AI transcript in graph")

        try:
            await run_relay(
                ws,
                on_session_update=on_session_update,
                on_user_transcript=on_user_transcript,
                on_ai_transcript=on_ai_transcript,
            )
        except Exception as e:
            logger.error(f"Error handling connection: {e}")

        return ws

    # ── HTTP エンドポイント ──

    async def handle_register_bot(self, request: web.Request):
        """Recall.ai bot_id を登録する."""
        try:
            body = await request.json()
            bot_id = body.get("bot_id")
            if not bot_id:
                return web.Response(status=400, text="bot_id is required")
            self.recall_bot_id = bot_id
            logger.debug(f"Registered Recall.ai bot_id: {bot_id}")
            return web.json_response({"status": "ok", "bot_id": bot_id})
        except Exception:
            logger.exception("Error registering bot")
            return web.Response(status=500, text="Internal server error")

    async def handle_get_instruction(self, request: web.Request):
        """シナリオに対応するプロンプトを返す."""
        scenario = request.query.get("scenario", "exit_interview")
        prompt_data = get_prompt(scenario)
        return web.Response(text=prompt_data.main_prompt)

    # ── サーバー起動 ──

    async def serve(self):
        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get("/", self.handle_browser_connection)
        app.router.add_get("/get-instruction", self.handle_get_instruction)
        app.router.add_post("/register-bot", self.handle_register_bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        logger.info(f"Server started on http://0.0.0.0:{PORT} and ws://0.0.0.0:{PORT}")
        await asyncio.Future()


def main():
    relay = WebSocketRelay()
    try:
        asyncio.run(relay.serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    finally:
        logger.info("Server shutdown complete")


if __name__ == "__main__":
    main()
