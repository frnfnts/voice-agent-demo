import { useState, useRef, useCallback } from "react";
import { RealtimeClient } from "@openai/realtime-api-beta";
// @ts-expect-error - External library without type definitions
import { WavRecorder } from "../lib/wavtools/index.js";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";

export function useRealtimeConnection(relayServerUrl: string | null) {
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("disconnected");

  const clientRef = useRef<RealtimeClient | null>(null);
  const wavRecorderRef = useRef<WavRecorder | null>(null);
  const isConnectedRef = useRef(false);

  if (!clientRef.current) {
    clientRef.current = new RealtimeClient({
      url: relayServerUrl || undefined,
    });
  }
  if (!wavRecorderRef.current) {
    wavRecorderRef.current = new WavRecorder({ sampleRate: 24000 });
  }

  const connect = useCallback(async () => {
    if (isConnectedRef.current) return;
    isConnectedRef.current = true;
    setConnectionStatus("connecting");

    const client = clientRef.current;
    const wavRecorder = wavRecorderRef.current;
    if (!client || !wavRecorder) return;

    try {
      await wavRecorder.begin();
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
        { type: "input_text", text: "こんにちは!" },
      ]);

      if (wavRecorder.recording) {
        await wavRecorder.pause();
      }
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

  const stopRecording = useCallback(() => {
    if (wavRecorderRef.current?.recording) {
      wavRecorderRef.current.pause().catch(() => {});
    }
  }, []);

  const disconnect = useCallback(() => {
    stopRecording();
    clientRef.current?.disconnect();
    setConnectionStatus("disconnected");
  }, [stopRecording]);

  return {
    client: clientRef.current,
    connectionStatus,
    setConnectionStatus,
    connect,
    disconnect,
    stopRecording,
  };
}
