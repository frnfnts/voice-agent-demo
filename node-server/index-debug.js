import { WebSocketServer } from "ws";
import { RealtimeClient } from "@openai/realtime-api-beta";
import dotenv from "dotenv";

dotenv.config();

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!OPENAI_API_KEY) {
  console.error(
    `Environment variable "OPENAI_API_KEY" is required.\n` +
      `Please set it in your .env file.`
  );
  process.exit(1);
}

const PORT = 3000;
const LOG_LEVEL = process.env.LOG_LEVEL || "debug";
const DEBUG_ENABLED = LOG_LEVEL === "debug";
const wss = new WebSocketServer({ port: PORT });

function debugLogEvent(event) {
  if (!DEBUG_ENABLED) return;

  if (event.audio) {
    event.audio = event.audio.substring(0, 100) + "...";
  }
  if (event.session?.instructions) {
    event.session.instructions = event.session.instructions.substring(0, 100) + "...";
  }

  console.debug(JSON.stringify(event, null, 2));
}

wss.on("connection", async (ws, req) => {
  if (!req.url) {
    console.log("No URL provided, closing connection.");
    ws.close();
    return;
  }

  const url = new URL(req.url, `https://${req.headers.host}`);
  const pathname = url.pathname;

  if (pathname !== "/") {
    console.log(`Invalid pathname: "${pathname}"`);
    ws.close();
    return;
  }

  const client = new RealtimeClient({ apiKey: OPENAI_API_KEY });

  client.realtime.on("server.*", (event) => {
    console.log(`Relaying "${event.type}" to Client`);
    if (event.type === "error") {
      console.error(`Error from OpenAI: ${JSON.stringify(event.error)}`);
      client.disconnect();
      ws.close();
      return;
    }
    if (event.type === "response.done" && event.response?.status === "failed") {
      console.error(`Response failed: ${JSON.stringify(event.response.status_details?.error)}`);
      client.disconnect();
      ws.close();
      return;
    }
    debugLogEvent(event);
    ws.send(JSON.stringify(event));
  });
  client.realtime.on("close", () => ws.close());

  const messageQueue = [];
  const messageHandler = (data) => {
    try {
      const event = JSON.parse(data);
      console.log(`Relaying "${event.type}" to OpenAI`);

      if (event.type === "input_audio_buffer.append" && event.audio) {
        console.log(`[DEBUG] Audio data length: ${event.audio.length}`);
        console.log(`[DEBUG] Audio data first 50 chars: ${event.audio.substring(0, 50)}`);
        console.log(`[DEBUG] Is valid base64: ${/^[A-Za-z0-9+/]*={0,2}$/.test(event.audio)}`);
      }

      debugLogEvent(event);
      client.realtime.send(event.type, event);
    } catch (e) {
      console.error(e.message);
      console.log(`Error parsing event from client: ${data}`);
    }
  };
  ws.on("message", (data) => {
    if (!client.isConnected()) {
      messageQueue.push(data);
    } else {
      messageHandler(data);
    }
  });
  ws.on("close", () => client.disconnect());

  try {
    console.log(`Connecting to OpenAI...`);
    await client.connect();
  } catch (e) {
    console.log(`Error connecting to OpenAI: ${e.message}`);
    ws.close();
    return;
  }
  console.log(`Connected to OpenAI successfully!`);
  while (messageQueue.length) {
    messageHandler(messageQueue.shift());
  }
});

console.log(`Websocket server listening on port ${PORT}`);