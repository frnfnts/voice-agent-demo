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

type InterviewStepState = {
  current_step: number;
  deep_dive_count: number;
  is_complete: boolean;
  step_summaries: Record<number, string>;
};

export function App() {
  const params = new URLSearchParams(window.location.search);
  const RELAY_SERVER_URL = params.get("wss");
  const BACKEND_URL = RELAY_SERVER_URL
    ? RELAY_SERVER_URL.replace("wss://", "https://").replace("ws://", "http://")
    : null;
  const IS_DEBUG = params.get("debug") === "true";
  const SCENARIO = params.get("scenario") || "exit_interview";
  const SCENARIO_LABELS: Record<string, string> = {
    exit_interview: "退職面談",
    compliance: "コンプライアンス通報受付",
  };
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
  const [interviewState, setInterviewState] = useState<InterviewStepState | null>(null);

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
        `${BACKEND_URL}/get-instruction?scenario=${SCENARIO}`,
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

      // LangGraph interview state events (debug mode only)
      if (IS_DEBUG) {
        // ws が確立されるまで待ってから addEventListener で受信する
        const waitForWs = () => {
          const ws = client.realtime.ws as WebSocket | undefined;
          if (!ws) {
            setTimeout(waitForWs, 100);
            return;
          }
          ws.addEventListener("message", (ev: MessageEvent) => {
            try {
              const data = JSON.parse(ev.data);
              if (data.type === "interview.state") {
                setInterviewState(data);
                setDebugLogs((logs) => [
                  ...logs,
                  `Step ${data.current_step} | dive=${data.deep_dive_count} | done=${data.is_complete}`,
                ]);
              }
            } catch {
              console.warn("Received non-JSON message, ignoring");
              setInstructionInfo((info) => ({
                ...info,
                warn: "Received non-JSON message from server, ignoring.",
              }));
              // not JSON or not our event, ignore
            }
          });
        };
        waitForWs();
      }

      return () => {
        client.reset();
      };
    }
  })()}, [instructionInfo?.error]);

  useEffect(() => {
    // Set instructions as JSON containing instruction, scenario, is_debug
    const client = clientRef.current;
    const payload = JSON.stringify({
      instruction: instructionInfo.text || "",
      scenario: SCENARIO,
      is_debug: IS_DEBUG,
    });
    client?.updateSession({ instructions: payload });
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

  const STEP_LABELS: Record<string, string[]> = {
    exit_interview: [
      "趣旨説明",
      "入社理由",
      "ギャップ",
      "きっかけ",
      "決め手",
      "改善可能性",
      "終了",
    ],
    compliance: [
      "趣旨説明",
      "概要把握",
      "5W1H",
      "証拠確認",
      "影響範囲",
      "希望・懸念",
      "終了",
    ],
  };
  const stepLabels = STEP_LABELS[SCENARIO] || STEP_LABELS.exit_interview;

  return (
    <div className={`app-container${IS_DEBUG ? ' debug' : ''}`}>
      <div className={`status-panel${IS_DEBUG ? ' debug' : ''}`}>
        <div className="icon-status">
          <img className={`status-icon-large ${statusClass}${IS_DEBUG ? ' debug' : ''}`} src={ayaIcon} alt="aya" />
          <div className={`status-state ${statusClass}`}>{statusLabel}</div>
          <div className="scenario-label">{SCENARIO_LABELS[SCENARIO] || SCENARIO}</div>
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
            <div className="debug-block">
              <div className="debug-subtitle">Interview Progress</div>
              <div className="step-indicator">
                {stepLabels.map((label, i) => {
                  const current = interviewState?.current_step;
                  const cls =
                    i < (current || 0)
                      ? "step-done"
                      : i === (current || 0)
                      ? "step-active"
                      : "step-pending";
                  return (
                    <div key={i} className={`step-chip ${cls}`}>
                      <span className="step-num">{i}</span>
                      <span className="step-label">{label}</span>
                    </div>
                  );
                })}
              </div>
              <div className="debug-mono" style={{ marginTop: 4 }}>
                deep_dive: {interviewState?.deep_dive_count} | complete: {String(interviewState?.is_complete)}
              </div>
            </div>
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
