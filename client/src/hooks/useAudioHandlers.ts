import { useEffect, useRef } from "react";
import { RealtimeClient } from "@openai/realtime-api-beta";
// @ts-expect-error - External library without type definitions
import { WavRecorder, WavStreamPlayer } from "../lib/wavtools/index.js";

export function useAudioHandlers(client: RealtimeClient | null) {
  const wavStreamPlayerRef = useRef<WavStreamPlayer | null>(null);
  if (!wavStreamPlayerRef.current) {
    wavStreamPlayerRef.current = new WavStreamPlayer({ sampleRate: 24000 });
  }

  useEffect(() => {
    if (!client) return;
    const wavStreamPlayer = wavStreamPlayerRef.current;
    if (!wavStreamPlayer) return;

    let connected = false;

    wavStreamPlayer.connect().then(() => {
      connected = true;
    });

    const onInterrupted = async () => {
      const trackSampleOffset = await wavStreamPlayer.interrupt();
      if (trackSampleOffset?.trackId) {
        const { trackId, offset } = trackSampleOffset;
        await client.cancelResponse(trackId, offset);
      }
    };

    const onUpdated = async ({ item, delta }: any) => {
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
    };

    client.on("error", (event: any) => console.error(event));
    client.on("conversation.interrupted", onInterrupted);
    client.on("conversation.updated", onUpdated);

    return () => {
      client.off("conversation.interrupted", onInterrupted);
      client.off("conversation.updated", onUpdated);
      if (connected) {
        wavStreamPlayer.interrupt();
      }
    };
  }, [client]);
}
