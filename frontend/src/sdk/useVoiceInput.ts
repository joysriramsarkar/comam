/**
 * useVoiceInput — the ONE voice-capture path for comam (MASTER-PLAN MP-10).
 *
 * Standalone App and the SDK widget used to grow separate microphone
 * code (different engines, different bugs). This hook replaces both:
 *
 *   push-to-talk -> MediaRecorder -> server ASR (billing-final text)
 *
 * Web Speech, when present, is used ONLY as a live interim preview —
 * its text is displayed, never submitted — unless no server transcribe
 * function was provided at all, in which case it is the degraded
 * fallback and the caller must show the transcript for confirmation.
 *
 * Browser uploads are converted to 24 kHz mono WAV in the browser
 * (MP-21), so the server needs no ffmpeg for the normal path and
 * Safari's audio/mp4 cannot break it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  assessClip,
  extensionForMime,
  levelKind as levelKindOf,
  pickMimeType,
  voiceErrorMessage,
  type LevelKind,
} from "./voice-utils";

export {
  assessClip,
  extensionForMime,
  pickMimeType,
  voiceErrorMessage,
  MIME_CANDIDATES,
  type ClipAssessment,
  type LevelKind,
} from "./voice-utils";

export type VoiceState = "idle" | "requesting" | "listening" | "processing" | "error";

export interface VoiceTranscribeResult {
  text: string;
  warnings?: string[];
}

export interface UseVoiceInputOptions {
  /** Server ASR call. Omit only for the degraded Web-Speech fallback. */
  transcribe?: (blob: Blob, filename: string) => Promise<VoiceTranscribeResult>;
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
  /** Live Web Speech caption while recording (never submitted). */
  enablePreview?: boolean;
  maxDurationSec?: number;
}

/** Decode any recorded blob and re-encode as 24 kHz mono 16-bit WAV. */
export async function toWav24k(blob: Blob): Promise<Blob> {
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AC();
  try {
    const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    const rate = 24000;
    const len = Math.max(1, Math.ceil(decoded.duration * rate));
    const off = new OfflineAudioContext(1, len, rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const pcm = (await off.startRendering()).getChannelData(0);
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const v = new DataView(buf);
    const writeStr = (o: number, s: string) =>
      [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    writeStr(0, "RIFF");
    v.setUint32(4, 36 + pcm.length * 2, true);
    writeStr(8, "WAVEfmt ");
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    writeStr(36, "data");
    v.setUint32(40, pcm.length * 2, true);
    pcm.forEach((s, i) =>
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true)
    );
    return new Blob([buf], { type: "audio/wav" });
  } finally {
    ctx.close().catch(() => {});
  }
}

type AnyRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((e: never) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function speechCtor(): (new () => AnyRecognition) | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as {
    SpeechRecognition?: new () => AnyRecognition;
    webkitSpeechRecognition?: new () => AnyRecognition;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition;
}

const DEVICE_KEY = "comam-mic-device";

export function useVoiceInput(options: UseVoiceInputOptions) {
  const { transcribe, onTranscript, onError, enablePreview = false } = options;
  const maxMs = (options.maxDurationSec ?? 30) * 1000;

  const [state, setState] = useState<VoiceState>("idle");
  const [level, setLevel] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [interimText, setInterimText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceIdState] = useState<string>(
    () => localStorage.getItem(DEVICE_KEY) || ""
  );
  const [lastAudioUrl, setLastAudioUrl] = useState<string | null>(null);
  /** True when the final text came from Web Speech, not server ASR. */
  const [degraded, setDegraded] = useState(false);

  const streamRef = useRef<MediaStream | null>(null);
  const streamDeviceRef = useRef<string>("__none__");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const levelTimerRef = useRef<number | null>(null);
  const maxTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const peakRef = useRef(0);
  const busyRef = useRef(false); // double-start / double-submit lock
  const previewRef = useRef<AnyRecognition | null>(null);
  const fallbackRef = useRef<AnyRecognition | null>(null);
  const fallbackFinalRef = useRef("");
  const unmountedRef = useRef(false);

  const optsRef = useRef(options);
  optsRef.current = options;

  const fail = useCallback((message: string) => {
    if (!message) return;
    setError(message);
    setState("error");
    optsRef.current.onError?.(message);
  }, []);

  const refreshDevices = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "audioinput"));
    } catch {
      /* device list is a nicety, not a blocker */
    }
  }, []);

  const ensureStream = useCallback(async (): Promise<MediaStream> => {
    const want = deviceId || "";
    if (
      streamRef.current?.active &&
      streamDeviceRef.current === want
    ) {
      return streamRef.current;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    const base: MediaTrackConstraints = {
      echoCancellation: { ideal: true },
      noiseSuppression: { ideal: true },
      autoGainControl: { ideal: true },
      channelCount: 1,
    };
    if (want) base.deviceId = { exact: want };
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: base });
    } catch {
      // Over-constrained device (unplugged since selection): plain audio.
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }
    streamRef.current = stream;
    streamDeviceRef.current = want;
    refreshDevices();
    return stream;
  }, [deviceId, refreshDevices]);

  const stopLevelLoop = useCallback(() => {
    if (levelTimerRef.current) window.clearInterval(levelTimerRef.current);
    levelTimerRef.current = null;
    if (maxTimerRef.current) window.clearTimeout(maxTimerRef.current);
    maxTimerRef.current = null;
    setLevel(0);
  }, []);

  const startLevelLoop = useCallback(
    (stream: MediaStream) => {
      try {
        const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const ctx = new AC();
        audioCtxRef.current = ctx;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        analyserRef.current = analyser;
        const data = new Uint8Array(analyser.fftSize);
        levelTimerRef.current = window.setInterval(() => {
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          let peak = 0;
          for (const b of data) {
            const x = (b - 128) / 128;
            sum += x * x;
            peak = Math.max(peak, Math.abs(x));
          }
          peakRef.current = Math.max(peakRef.current, peak);
          setLevel(Math.min(1, Math.sqrt(sum / data.length) * 3));
          setElapsedSec(Math.floor((Date.now() - startedAtRef.current) / 1000));
        }, 100);
      } catch {
        /* meter is feedback only */
      }
    },
    []
  );

  const stopPreview = useCallback(() => {
    try {
      previewRef.current?.stop();
    } catch { /* already stopped */ }
    previewRef.current = null;
  }, []);

  const startPreview = useCallback(() => {
    const Ctor = speechCtor();
    if (!Ctor || previewRef.current) return;
    try {
      const rec = new Ctor();
      rec.lang = "bn-BD";
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      rec.onresult = (e: never) => {
        const ev = e as unknown as {
          results: { [i: number]: { [j: number]: { transcript: string }; isFinal: boolean; length: number }; length: number };
        };
        let txt = "";
        for (let i = 0; i < ev.results.length; i++) txt += ev.results[i][0].transcript;
        setInterimText(txt); // display only — never submitted
      };
      rec.onerror = () => stopPreview(); // preview failure is silent
      rec.onend = () => { previewRef.current = null; };
      previewRef.current = rec;
      rec.start();
    } catch {
      previewRef.current = null;
    }
  }, [stopPreview]);

  const finishRecording = useCallback(async () => {
    const durationMs = Date.now() - startedAtRef.current;
    stopLevelLoop();
    stopPreview();
    const mime = recorderRef.current?.mimeType || "audio/webm";
    recorderRef.current = null;
    const blob = new Blob(chunksRef.current, { type: mime });
    chunksRef.current = [];
    if (lastAudioUrl) URL.revokeObjectURL(lastAudioUrl);
    setLastAudioUrl(URL.createObjectURL(blob));

    const verdict = assessClip(durationMs, peakRef.current);
    if (verdict !== "ok") {
      busyRef.current = false;
      fail(
        verdict === "too_short"
          ? "রেকর্ডিং খুব ছোট — আরেকটু সময় ধরে বলুন।"
          : "আওয়াজ পাইনি — মাইকের কাছে এসে আরেকটু জোরে বলুন।"
      );
      return;
    }
    const tx = optsRef.current.transcribe;
    if (!tx) {
      busyRef.current = false;
      fail("সার্ভার ভয়েস সার্ভিস যুক্ত নেই — লিখে কমান্ড দিন।");
      return;
    }
    setState("processing");
    try {
      let upload = blob;
      let filename = `audio.${extensionForMime(mime)}`;
      try {
        upload = await toWav24k(blob);
        filename = "audio.wav";
      } catch {
        /* keep the original blob; the server converts when it can */
      }
      const result = await tx(upload, filename);
      busyRef.current = false;
      if (unmountedRef.current) return;
      const text = (result.text || "").trim();
      if (text) {
        setDegraded(false);
        setState("idle");
        optsRef.current.onTranscript(text);
      } else {
        fail(voiceErrorMessage("no-speech"));
      }
    } catch (e) {
      busyRef.current = false;
      if (unmountedRef.current) return;
      fail(e instanceof Error ? e.message : voiceErrorMessage("network"));
    }
  }, [fail, lastAudioUrl, stopLevelLoop, stopPreview]);

  // Degraded path: no server ASR — Web Speech is all we have. Its final
  // text IS delivered, flagged via `degraded`, so the UI can insist on
  // a read-back confirmation before anything is billed.
  const startFallback = useCallback(() => {
    const Ctor = speechCtor();
    if (!Ctor) {
      busyRef.current = false;
      fail("আপনার ব্রাউজারে স্পিচ রিকগনিশন সমর্থিত নয়। দয়া করে লিখে কমান্ড দিন।");
      return;
    }
    try {
      const rec = new Ctor();
      rec.lang = "bn-BD";
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 5;
      fallbackFinalRef.current = "";
      rec.onresult = (e: never) => {
        const ev = e as unknown as {
          resultIndex: number;
          results: { [i: number]: { [j: number]: { transcript: string }; isFinal: boolean }; length: number };
        };
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const item = ev.results[i];
          if (item.isFinal) fallbackFinalRef.current += item[0].transcript;
          else interim += item[0].transcript;
        }
        setInterimText((fallbackFinalRef.current + interim).trim());
      };
      rec.onerror = (ev: { error: string }) => {
        if (ev.error !== "aborted") fail(voiceErrorMessage(ev.error));
      };
      rec.onend = () => {
        fallbackRef.current = null;
        busyRef.current = false;
        if (unmountedRef.current) return;
        setState((s) => (s === "listening" ? "idle" : s));
        const text = fallbackFinalRef.current.trim();
        if (text) {
          setDegraded(true);
          optsRef.current.onTranscript(text);
        }
      };
      fallbackRef.current = rec;
      setState("listening");
      rec.start();
    } catch {
      busyRef.current = false;
      fail(voiceErrorMessage("audio-capture"));
    }
  }, [fail]);

  const start = useCallback(async () => {
    if (busyRef.current) return; // double-start lock
    busyRef.current = true;
    setError(null);
    setInterimText("");
    setElapsedSec(0);
    peakRef.current = 0;

    if (!optsRef.current.transcribe) {
      startFallback();
      return;
    }
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      startFallback();
      return;
    }
    setState("requesting");
    try {
      const stream = await ensureStream();
      if (unmountedRef.current) { busyRef.current = false; return; }
      const mime = pickMimeType((t) => MediaRecorder.isTypeSupported(t));
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = () => { void finishRecording(); };
      recorderRef.current = rec;
      startedAtRef.current = Date.now();
      rec.start(250);
      startLevelLoop(stream);
      if (enablePreview) startPreview();
      maxTimerRef.current = window.setTimeout(() => {
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      }, maxMs);
      setState("listening");
    } catch (e) {
      busyRef.current = false;
      const name = (e as { name?: string })?.name;
      fail(voiceErrorMessage(name === "NotAllowedError" ? "not-allowed" : "audio-capture"));
    }
  }, [enablePreview, ensureStream, fail, finishRecording, maxMs, startFallback, startLevelLoop, startPreview]);

  const stop = useCallback(() => {
    if (fallbackRef.current) {
      try { fallbackRef.current.stop(); } catch { /* noop */ }
      return; // onend delivers the transcript
    }
    if (recorderRef.current?.state === "recording") {
      // 400 ms tail padding so the last word is not clipped.
      window.setTimeout(() => {
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      }, 400);
    }
  }, []);

  const toggle = useCallback(() => {
    if (state === "listening") stop();
    else void start();
  }, [start, state, stop]);

  const setDeviceId = useCallback((id: string) => {
    setDeviceIdState(id);
    if (id) localStorage.setItem(DEVICE_KEY, id);
    else localStorage.removeItem(DEVICE_KEY);
    // Force a fresh stream on next start.
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    streamDeviceRef.current = "__none__";
  }, []);

  useEffect(() => {
    refreshDevices();
  }, [refreshDevices]);

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      busyRef.current = false;
      try { recorderRef.current?.stop(); } catch { /* noop */ }
      try { fallbackRef.current?.stop(); } catch { /* noop */ }
      stopPreview();
      stopLevelLoop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => {});
    };
  }, [stopLevelLoop, stopPreview]);

  const levelKind: LevelKind = levelKindOf(level);

  return {
    state,
    isListening: state === "listening",
    isProcessing: state === "processing",
    level,
    levelKind,
    elapsedSec,
    interimText,
    error,
    devices,
    deviceId,
    setDeviceId,
    lastAudioUrl,
    degraded,
    hasServerAsr: Boolean(transcribe),
    supported:
      typeof window !== "undefined" &&
      (Boolean(transcribe && typeof MediaRecorder !== "undefined") || Boolean(speechCtor())),
    start,
    stop,
    toggle,
  };
}
