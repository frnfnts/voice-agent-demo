import { useState, useEffect } from "react";
import { fallbackInstructions } from "../conversation_config.js";
import type { InstructionInfo } from "../types/messages.js";

export function useInstructions(
  backendUrl: string | null,
  scenario: string,
  addLog: (msg: string) => void,
  initialError?: string
) {
  const [instructionInfo, setInstructionInfo] = useState<InstructionInfo>({
    source: "none",
    error: initialError,
  });

  useEffect(() => {
    if (initialError || !backendUrl) return;

    let cancelled = false;

    (async () => {
      try {
        const response = await fetch(
          `${backendUrl}/get-instruction?scenario=${scenario}`,
          {
            headers: {
              "content-type": "text/plain",
              "ngrok-skip-browser-warning": "true",
            },
          }
        );
        if (cancelled) return;
        const text = await response.text();
        setInstructionInfo({ source: "google-drive", text });
        addLog("Fetched instructions from Google Drive.");
      } catch (error) {
        if (cancelled) return;
        console.error("Failed to fetch instructions from Drive:", error);
        setInstructionInfo({
          source: "fallback",
          text: fallbackInstructions,
          warn: "Failed to fetch instructions from Drive, using fallback.",
        });
        addLog("Using fallback instructions.");
        addLog(String(error));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [backendUrl, scenario, initialError, addLog]);

  return { instructionInfo };
}
