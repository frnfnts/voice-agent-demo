import { useState, useEffect, useCallback } from "react";
import "./App.css";
import ayaIcon from "../aya.png";
import { useRealtimeConnection } from "./hooks/useRealtimeConnection.js";
import { useInstructions } from "./hooks/useInstructions.js";
import { useInterviewState } from "./hooks/useInterviewState.js";
import { useAudioHandlers } from "./hooks/useAudioHandlers.js";
import { DebugPanel } from "./components/DebugPanel.js";
import { MessageStack } from "./components/MessageStack.js";

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
  test: "テスト（短縮）",
};

const STEP_LABELS: Record<string, string[]> = {
  exit_interview: ["趣旨説明", "入社理由", "ギャップ", "きっかけ", "決め手", "改善可能性", "終了"],
  compliance: ["趣旨説明", "概要把握", "5W1H", "証拠確認", "影響範囲", "希望・懸念", "終了"],
  test: ["挨拶", "うれしかったこと", "終了"],
};

function getInitialError(): string | undefined {
  if (!RELAY_SERVER_URL) return 'Missing required "wss" parameter in URL';
  try {
    new URL(RELAY_SERVER_URL);
    return undefined;
  } catch {
    return 'Invalid URL format for "wss" parameter';
  }
}

export function App() {
  const initialError = getInitialError();

  const [debugLogs, setDebugLogs] = useState<string[]>([]);
  const addLog = useCallback(
    (msg: string) => setDebugLogs((prev) => [...prev, msg]),
    []
  );

  const { client, connectionStatus, connect, stopRecording } =
    useRealtimeConnection(RELAY_SERVER_URL);

  const { instructionInfo } =
    useInstructions(BACKEND_URL, SCENARIO, addLog, initialError);

  // interview.complete 受信時はマイクのみ停止し、音声を最後まで再生させる。
  // WebSocket は DISCONNECT_DELAY 後にサーバー側で閉じられ、
  // disconnected イベントで connectionStatus が更新される。
  const onInterviewComplete = useCallback(
    () => stopRecording(),
    [stopRecording]
  );

  const { interviewState } = useInterviewState(
    client,
    IS_DEBUG,
    onInterviewComplete,
    addLog
  );

  // client が出力した音声を再生するハンドラー
  useAudioHandlers(client);

  // Connect on mount (if no error)
  useEffect(() => {
    if (!initialError) {
      connect();
    }
  }, [initialError, connect]);

  // Send session config when instructions are ready
  useEffect(() => {
    if (!client) return;
    const payload = JSON.stringify({
      instruction: instructionInfo.text || "",
      scenario: SCENARIO,
      is_debug: IS_DEBUG,
    });
    client.updateSession({
      // @ts-ignore
      type: "realtime",
      instructions: payload,
      modalities: ["text", "audio"],
      // @ts-ignore
      voice: "marin",
      // @ts-ignore
      speed: 0.9,
      turn_detection: {
        type: "server_vad",
        threshold: 0.7,
        silence_duration_ms: 1200,
        prefix_padding_ms: 500,
        // @ts-ignore
        create_response: false,
      },
      input_audio_transcription: {
        // @ts-ignore
        model: "gpt-4o-mini-transcribe",
      },
    });
  }, [instructionInfo, client]);

  const statusClass = instructionInfo?.error ? "error" : connectionStatus;
  const statusLabel = instructionInfo?.error
    ? "Error"
    : connectionStatus === "connecting"
    ? "Connecting"
    : connectionStatus === "connected"
    ? "Connected"
    : "Disconnected";
  const stepLabels = STEP_LABELS[SCENARIO] || STEP_LABELS.exit_interview;

  return (
    <div className={`app-container${IS_DEBUG ? " debug" : ""}`}>
      <div className={`status-panel${IS_DEBUG ? " debug" : ""}`}>
        <div className="icon-status">
          <img
            className={`status-icon-large ${statusClass}${IS_DEBUG ? " debug" : ""}`}
            src={ayaIcon}
            alt="aya"
          />
          <div className={`status-state ${statusClass}`}>{statusLabel}</div>
          <div className="scenario-label">
            {SCENARIO_LABELS[SCENARIO] || SCENARIO}
          </div>
        </div>

        <MessageStack instructionInfo={instructionInfo} />

        {IS_DEBUG && (
          <DebugPanel
            interviewState={interviewState}
            instructionInfo={instructionInfo}
            debugLogs={debugLogs}
            stepLabels={stepLabels}
          />
        )}
      </div>
    </div>
  );
}

export default App;
