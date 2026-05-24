import { useEffect, useRef } from "react";
// @ts-expect-error - External library without type definitions
import { WavStreamPlayer } from "../lib/wavtools/index.js";

export function useAudioHandlers(ws: WebSocket | null) {
  const wavStreamPlayerRef = useRef<WavStreamPlayer | null>(null);
  // Store the connect() promise so it's called exactly once across strict-mode remounts
  const connectPromiseRef = useRef<Promise<boolean> | null>(null);

  if (!wavStreamPlayerRef.current) {
    wavStreamPlayerRef.current = new WavStreamPlayer({ sampleRate: 24000 });
  }

  // Connect player once on mount (independent of ws lifecycle)
  useEffect(() => {
    const player = wavStreamPlayerRef.current!;
    if (!connectPromiseRef.current) {
      connectPromiseRef.current = player
        .connect()
        .catch((e: unknown) => {
          console.error("WavStreamPlayer connect error:", e);
          connectPromiseRef.current = null;
          return false;
        });
    }
  }, []);

  // Register message listener when ws changes
  useEffect(() => {
    if (!ws) return;
    const player = wavStreamPlayerRef.current!;
    let active = true;

    const handleMessage = (event: MessageEvent) => {
      let data: any;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === "response.audio.delta" && data.delta) {
        try {
          const binary = atob(data.delta);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          player.add16BitPCM(bytes.buffer, data.item_id);
        } catch (e) {
          console.error("[audio] playback error:", e);
        }
      }

      if (data.type === "input_audio_buffer.speech_started") {
        player.interrupt().then((trackSampleOffset: any) => {
          if (!active || !trackSampleOffset?.trackId) return;
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({
                type: "conversation.item.truncate",
                item_id: trackSampleOffset.trackId,
                content_index: 0,
                audio_end_ms: Math.floor(
                  (trackSampleOffset.offset / 24000) * 1000
                ),
              })
            );
          }
        });
      }
    };

    // Wait for connect() to finish before registering listener (ensures analyser is set)
    const pending = connectPromiseRef.current ?? Promise.resolve();
    pending.then(() => {
      if (active) ws.addEventListener("message", handleMessage);
    });

    return () => {
      active = false;
      ws.removeEventListener("message", handleMessage);
      player.interrupt();
    };
  }, [ws]);
}
