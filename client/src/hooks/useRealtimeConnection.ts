import { useState, useRef, useCallback } from "react";
// @ts-expect-error - External library without type definitions
import { WavRecorder } from "../lib/wavtools/index.js";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";

// WavRecorder の record コールバックが渡す data.mono は、
// 既に PCM16 (little-endian) にエンコード済みの ArrayBuffer。
// そのまま base64 化して input_audio_buffer.append で送る。
function pcm16ArrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

export function useRealtimeConnection(relayServerUrl: string | null) {
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("disconnected");
  const [ws, setWs] = useState<WebSocket | null>(null);
  const wavRecorderRef = useRef<WavRecorder | null>(null);
  const isConnectedRef = useRef(false);

  if (!wavRecorderRef.current) {
    wavRecorderRef.current = new WavRecorder({ sampleRate: 24000 });
  }

  const connect = useCallback(async () => {
    if (isConnectedRef.current || !relayServerUrl) return;
    isConnectedRef.current = true;
    setConnectionStatus("connecting");

    const wavRecorder = wavRecorderRef.current!;

    try {
      await wavRecorder.begin();

      await new Promise<void>((resolve, reject) => {
        const newWs = new WebSocket(relayServerUrl, ["realtime"]);

        newWs.onerror = () => {
          setConnectionStatus("disconnected");
          isConnectedRef.current = false;
          reject(new Error("WebSocket connection error"));
        };

        newWs.onclose = () => {
          setConnectionStatus("disconnected");
          isConnectedRef.current = false;
          setWs(null);
        };

        newWs.onopen = async () => {
          setConnectionStatus("connected");
          setWs(newWs);
          if (wavRecorder.recording) await wavRecorder.pause();
          await wavRecorder.record((data: { mono: ArrayBuffer }) => {
            if (newWs.readyState === WebSocket.OPEN && data.mono.byteLength > 0) {
              newWs.send(
                JSON.stringify({
                  type: "input_audio_buffer.append",
                  audio: pcm16ArrayBufferToBase64(data.mono),
                })
              );
            }
          });
          resolve();
        };
      });
    } catch (error) {
      console.error("Connection error:", error);
      setConnectionStatus("disconnected");
      isConnectedRef.current = false;
    }
  }, [relayServerUrl]);

  const stopRecording = useCallback(() => {
    if (wavRecorderRef.current?.recording) {
      wavRecorderRef.current.pause().catch(() => {});
    }
  }, []);

  const disconnect = useCallback(() => {
    stopRecording();
    ws?.close(1000, "Normal closure");
  }, [stopRecording, ws]);

  return {
    ws,
    connectionStatus,
    connect,
    disconnect,
    stopRecording,
    wavRecorderRef,
  };
}
