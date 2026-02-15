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
COMPLIANCE_INSTRUCTION_DOC_ID = os.getenv("COMPLIANCE_INSTRUCTION_DOC_ID", "1cQSHjpoijqEkbvU8h5ZlMzk3qIdy6u4gjL4qXM4BA9w")

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
    if "session" in truncated and isinstance(truncated.get("session"), dict):
        if "instructions" in truncated["session"] and isinstance(truncated["session"]["instructions"], str) and len(truncated["session"]["instructions"]) > 100:
            truncated["session"]["instructions"] = truncated["session"]["instructions"][:100] + "..."

    logger.debug(f"{message}: {json.dumps(truncated, indent=2)}")


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


class WebSocketRelay:
    def __init__(self):
        """Initialize the WebSocket relay server."""
        self.connections = {}

    async def handle_browser_connection(self, request: web.Request):
        """Handle a WebSocket connection from the browser."""
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return web.Response(text="OK")

        ws = web.WebSocketResponse(protocols=("realtime",))
        await ws.prepare(request)

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
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        message = msg.data
                        try:
                            event = json.loads(message)
                            logger.info(f'Relaying "{event.get("type")}" to OpenAI')
                            debug_log_event("Browser -> OpenAI", event)
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
