import { useState, useEffect, useRef, useCallback } from "react";
import { RealtimeClient } from "@openai/realtime-api-beta";
// @ts-expect-error - External library without type definitions
import { WavRecorder, WavStreamPlayer } from "./lib/wavtools/index.js";
import {
  fallbackInstructions,
} from "./conversation_config.js";
import "./App.css";
import ayaIcon from "../aya.png";

const clientRef = { current: null as RealtimeClient | null };
const wavRecorderRef = { current: null as WavRecorder | null };
const wavStreamPlayerRef = { current: null as WavStreamPlayer | null };

type InstructionInfo = {
  source: "google-drive" | "fallback" | "none";
  text?: string;
  error?: string;
  warn?: string;
};

export function App() {
  const params = new URLSearchParams(window.location.search);
  const RELAY_SERVER_URL = params.get("wss");
  const BACKEND_URL = RELAY_SERVER_URL
    ? RELAY_SERVER_URL.replace("wss://", "https://").replace("ws://", "http://")
    : null;
  const IS_DEBUG = params.get("debug") === "true";
  const [connectionStatus, setConnectionStatus] = useState<
    "disconnected" | "connecting" | "connected"
  >("disconnected");
  const [instructionInfo, setInstructionInfo] = useState<InstructionInfo>({
    source: "none",
    error: (() => {
        if (!RELAY_SERVER_URL) {
            return 'Missing required "wss" parameter in URL';
        }
        try {
          new URL(RELAY_SERVER_URL);
          return;
        } catch {
          return 'Invalid URL format for "wss" parameter';
        }
      })(),
  });

  const [debugLogs, setDebugLogs] = useState<string[]>([]);

  if (!clientRef.current) {
    clientRef.current = new RealtimeClient({
      url: RELAY_SERVER_URL || undefined,
    });
  }
  if (!wavRecorderRef.current) {
    wavRecorderRef.current = new WavRecorder({ sampleRate: 24000 });
  }
  if (!wavStreamPlayerRef.current) {
    wavStreamPlayerRef.current = new WavStreamPlayer({ sampleRate: 24000 });
  }
  const isConnectedRef = useRef(false);
  const connectConversation = useCallback(async () => {
    if (isConnectedRef.current) return;
    isConnectedRef.current = true;
    setConnectionStatus("connecting");
    const client = clientRef.current;
    const wavRecorder = wavRecorderRef.current;
    const wavStreamPlayer = wavStreamPlayerRef.current;
    if (!client || !wavRecorder || !wavStreamPlayer) return;

    try {
      // Connect to microphone
      await wavRecorder.begin();

      // Connect to audio output
      await wavStreamPlayer.connect();

      // Connect to realtime API
      await client.connect();

      setConnectionStatus("connected");

      client.on("error", (event: any) => {
        console.error(event);
        setConnectionStatus("disconnected");
      });

      client.on("disconnected", () => {
        setConnectionStatus("disconnected");
      });

      client.sendUserMessageContent([
        {
          type: `input_text`,
          text: `こんにちは!`,
        },
      ]);

      // Always use VAD mode
      client.updateSession({
        turn_detection: { type: "server_vad" },
        // @ts-ignore  ライブラリが古いので marin が指定できないので無視する
        voice: 'marin',
      });

      // Check if we're already recording before trying to pause
      if (wavRecorder.recording) {
        await wavRecorder.pause();
      }

      // Check if we're already paused before trying to record
      if (!wavRecorder.recording) {
        await wavRecorder.record((data: { mono: Float32Array }) =>
          client.appendInputAudio(data.mono)
        );
      }
    } catch (error) {
      console.error("Connection error:", error);
      setConnectionStatus("disconnected");
    }
  }, []);

  /**
   * Core RealtimeClient and audio capture setup
   * Set all of our instructions, tools, events and more
   */
  useEffect(() => {(async () => {
    // Only run the effect if there's no error
    if (!instructionInfo?.error) {
      connectConversation();
      const wavStreamPlayer = wavStreamPlayerRef.current;
      const client = clientRef.current;
      if (!client || !wavStreamPlayer) return;

      const fetchedInstructions = await fetch(
        `${BACKEND_URL}/get-instruction`,
        {
          headers: {
            "content-type": "text/plain",
            "ngrok-skip-browser-warning": "true",
          },
        }
      ).catch((error) => {
        console.error("Failed to fetch instructions from Drive:", error);
        setInstructionInfo({
          source: "fallback",
          text: fallbackInstructions,
          warn: "Failed to fetch instructions from Drive, using fallback.",
        });
        setDebugLogs((logs) => [...logs, "Using fallback instructions.", error.toString()]);
      });

      if (fetchedInstructions) {
        console.log(fetchedInstructions);
        setInstructionInfo({
          source: "google-drive",
          text: await fetchedInstructions.body?.getReader().read().then(async ({ value }) => {
            const decoder = new TextDecoder("utf-8");
            return decoder.decode(value);
          }),
        });
        setDebugLogs((logs) => [...logs, "Fetched instructions from Google Drive."]);
      }

      // handle realtime events from client + server for event logging
      client.on("error", (event: any) => console.error(event));
      client.on("conversation.interrupted", async () => {
        const trackSampleOffset = await wavStreamPlayer.interrupt();
        if (trackSampleOffset?.trackId) {
          const { trackId, offset } = trackSampleOffset;
          await client.cancelResponse(trackId, offset);
        }
      });
      client.on("conversation.updated", async ({ item, delta }: any) => {
        client.conversation.getItems();
        if (delta?.audio) {
          wavStreamPlayer.add16BitPCM(delta.audio, item.id);
        }
        if (item.status === "completed" && item.formatted.audio?.length) {
          const wavFile = await WavRecorder.decode(
            item.formatted.audio,
            24000,
            24000
          );
          item.formatted.file = wavFile;
        }
      });

      return () => {
        client.reset();
      };
    }
  })()}, [instructionInfo?.error]);

  useEffect(() => {
    // Set instructions
    const client = clientRef.current;
    client?.updateSession({ instructions: instructionInfo.text || "" });
  }, [instructionInfo]);

  const statusClass = instructionInfo?.error ? "error" : connectionStatus;
  const statusLabel = instructionInfo?.error
    ? "Error"
    : connectionStatus === "connecting"
    ? "Connecting"
    : connectionStatus === "connected"
    ? "Connected"
    : "Disconnected";
  const instructionLength = instructionInfo?.text?.length || 0;

  return (
    <div className="app-container">
      <div className="status-panel">
        <div className="icon-status">
          <img className={`status-icon-large ${statusClass}`} src={ayaIcon} alt="aya" />
          <div className={`status-state ${statusClass}`}>{statusLabel}</div>
        </div>

        {(instructionInfo?.warn || instructionInfo?.error) && (
          <div className="message-stack">
            {instructionInfo?.warn && (
              <div className="message-box warn">
                <div className="message-title">Warning</div>
                <div className="message-body">{instructionInfo.warn}</div>
              </div>
            )}
            {instructionInfo?.error && (
              <div className="message-box error">
                <div className="message-title">Error</div>
                <div className="message-body">{instructionInfo.error}</div>
              </div>
            )}
          </div>
        )}

        {IS_DEBUG && (
          <div className="debug-panel">
            <div className="debug-title">Debug</div>
            <div className="debug-grid">
              <div className="debug-row">
                <div className="debug-key">instructionSource</div>
                <div className="debug-value">{instructionInfo?.source}</div>
              </div>
            </div>
            <div className="debug-block">
              <div className="debug-subtitle">Instruction Preview</div>
              <div className="debug-mono">
                {(instructionInfo?.text || "").slice(0, 100)}
                {instructionLength > 100 ? "..." : ""}
              </div>
            </div>
            <div className="debug-block">
              <div className="debug-subtitle">Logs</div>
              <div className="debug-mono">
                {debugLogs.length ? debugLogs.join("\n") : "(no logs)"}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
