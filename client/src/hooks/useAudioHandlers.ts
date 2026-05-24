import { useEffect, useRef } from "react";
// @ts-expect-error - External library without type definitions
import { WavStreamPlayer } from "../lib/wavtools/index.js";

export function useAudioHandlers(ws: WebSocket | null) {
  const wavStreamPlayerRef = useRef<WavStreamPlayer | null>(null);
  if (!wavStreamPlayerRef.current) {
    wavStreamPlayerRef.current = new WavStreamPlayer({ sampleRate: 24000 });
  }

  useEffect(() => {
    if (!ws) return;
    const player = wavStreamPlayerRef.current!;
    player.connect();

    const handleMessage = async (event: MessageEvent) => {
      let data: any;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === "response.audio.delta" && data.delta) {
        const binary = atob(data.delta);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        player.add16BitPCM(bytes.buffer, data.item_id);
      }

      if (data.type === "input_audio_buffer.speech_started") {
        const trackSampleOffset = await player.interrupt();
        if (trackSampleOffset?.trackId && ws.readyState === WebSocket.OPEN) {
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
      }
    };

    ws.addEventListener("message", handleMessage);
    return () => {
      ws.removeEventListener("message", handleMessage);
      player.interrupt();
    };
  }, [ws]);
}
