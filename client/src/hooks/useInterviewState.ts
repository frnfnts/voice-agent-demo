import { useState, useEffect } from "react";
import type { InterviewStepState } from "../types/messages.js";

export function useInterviewState(
  ws: WebSocket | null,
  isDebug: boolean,
  onDisconnect: () => void,
  addLog: (msg: string) => void
) {
  const [interviewState, setInterviewState] =
    useState<InterviewStepState | null>(null);

  useEffect(() => {
    if (!ws) return;

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
      }
    };

    ws.addEventListener("message", handler);
    return () => ws.removeEventListener("message", handler);
  }, [ws, isDebug, onDisconnect, addLog]);

  return { interviewState };
}
