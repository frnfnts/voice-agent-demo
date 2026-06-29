import { useEffect, useRef, useState } from "react";

const SILENCE_THRESHOLD = 0.01;
const MUTE_WARNING_DELAY_MS = 8000;
const CONNECT_GRACE_MS = 3000;

export function useUserVoiceActivity(
  wavRecorderRef: React.RefObject<any>,
  isConnected: boolean
): { isSpeaking: boolean; muteWarning: boolean } {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [muteWarning, setMuteWarning] = useState(false);

  const isSpeakingRef = useRef(false);
  const muteWarningRef = useRef(false);
  const lastSpeechTimeRef = useRef(Date.now());
  const connectTimeRef = useRef<number | null>(null);
  const dataArrayRef = useRef<Float32Array | null>(null);
  const rafRef = useRef<number | null>(null);
  const isConnectedRef = useRef(isConnected);

  // Track connection state without restarting the RAF loop
  useEffect(() => {
    isConnectedRef.current = isConnected;
    if (isConnected) {
      connectTimeRef.current = Date.now();
      lastSpeechTimeRef.current = Date.now();
    } else {
      connectTimeRef.current = null;
      if (muteWarningRef.current) {
        muteWarningRef.current = false;
        setMuteWarning(false);
      }
      if (isSpeakingRef.current) {
        isSpeakingRef.current = false;
        setIsSpeaking(false);
      }
    }
  }, [isConnected]);

  useEffect(() => {
    let active = true;

    const tick = () => {
      if (!active) return;

      const analyser = wavRecorderRef.current?.analyser;
      if (!analyser || !isConnectedRef.current) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      if (
        !dataArrayRef.current ||
        dataArrayRef.current.length !== analyser.fftSize
      ) {
        dataArrayRef.current = new Float32Array(analyser.fftSize);
      }

      analyser.getFloatTimeDomainData(dataArrayRef.current);

      let sum = 0;
      const data = dataArrayRef.current;
      for (let i = 0; i < data.length; i++) {
        sum += data[i] * data[i];
      }
      const rms = Math.sqrt(sum / data.length);

      const speaking = rms > SILENCE_THRESHOLD;
      if (speaking !== isSpeakingRef.current) {
        isSpeakingRef.current = speaking;
        setIsSpeaking(speaking);
      }

      const now = Date.now();
      if (speaking) {
        lastSpeechTimeRef.current = now;
        if (muteWarningRef.current) {
          muteWarningRef.current = false;
          setMuteWarning(false);
        }
      } else {
        const connectTime = connectTimeRef.current;
        if (connectTime !== null) {
          const sinceConnect = now - connectTime;
          const silenceDuration = now - lastSpeechTimeRef.current;
          const shouldWarn =
            sinceConnect > CONNECT_GRACE_MS &&
            silenceDuration > MUTE_WARNING_DELAY_MS;
          if (shouldWarn !== muteWarningRef.current) {
            muteWarningRef.current = shouldWarn;
            setMuteWarning(shouldWarn);
          }
        }
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      active = false;
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [wavRecorderRef]);

  return { isSpeaking, muteWarning };
}
