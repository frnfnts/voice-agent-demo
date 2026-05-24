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
          console.error("Audio playback error:", e);
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

    // connect() が完了してから listener を登録して this.analyser null エラーを防ぐ
    player
      .connect()
      .then(() => {
        if (active) ws.addEventListener("message", handleMessage);
      })
      .catch((e: unknown) => console.error("WavStreamPlayer connect error:", e));

    return () => {
      active = false;
      ws.removeEventListener("message", handleMessage);
      player.interrupt();
    };
  }, [ws]);
}
