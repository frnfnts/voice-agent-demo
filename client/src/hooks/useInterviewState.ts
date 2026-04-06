import { useState, useEffect } from "react";
import { RealtimeClient } from "@openai/realtime-api-beta";
import type { InterviewStepState } from "../types/messages.js";

export function useInterviewState(
  client: RealtimeClient | null,
  isDebug: boolean,
  onDisconnect: () => void,
  addLog: (msg: string) => void
) {
  const [interviewState, setInterviewState] =
    useState<InterviewStepState | null>(null);

  useEffect(() => {
    if (!client) return;

    const waitForWs = () => {
      const ws = (client as any).realtime?.ws as WebSocket | undefined;
      if (!ws) {
        const timer = setTimeout(waitForWs, 100);
        return () => clearTimeout(timer);
      }

      const handler = (ev: MessageEvent) => {
        try {
          const data = JSON.parse(ev.data);

          if (data.type === "interview.state" && isDebug) {
            setInterviewState(data);
            addLog(
              `Step ${data.current_step} | dive=${data.deep_dive_count} | done=${data.is_complete}`
            );
          }

          if (data.type === "interview.complete") {
            console.log("Interview complete — disconnecting");
            onDisconnect();
          }
        } catch {
          // not JSON, ignore
          addLog(`Received non-JSON message: ${ev.data}`);
        }
      };

      ws.addEventListener("message", handler);
      return () => ws.removeEventListener("message", handler);
    };

    const cleanup = waitForWs();
    return cleanup;
  }, [client, isDebug, onDisconnect, addLog]);

  return { interviewState };
}
