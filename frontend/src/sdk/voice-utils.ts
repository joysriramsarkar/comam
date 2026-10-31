/**
 * Pure voice-capture helpers (no React, no DOM) — unit-testable core
 * of useVoiceInput (MASTER-PLAN §6.1).
 */

export const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/mp4",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "",
];

export function pickMimeType(isSupported: (t: string) => boolean): string {
  return MIME_CANDIDATES.find((t) => t === "" || isSupported(t)) ?? "";
}

export function extensionForMime(mime: string): string {
  if (mime.includes("wav")) return "wav";
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

/** Appendix A of the master plan: every failure gets a Bengali reason. */
export function voiceErrorMessage(code: string): string {
  const map: Record<string, string> = {
    "not-allowed":
      "মাইকের অনুমতি দেওয়া হয়নি — ব্রাউজার/অ্যাপ সেটিংস থেকে Allow করুন, তারপর আবার বলুন।",
    "service-not-allowed": "এই ব্রাউজারে ভয়েস সার্ভিস বন্ধ আছে — লিখে কমান্ড দিন।",
    "audio-capture": "মাইক্রোফোন পাওয়া যাচ্ছে না — ডিভাইস বদলে আবার চেষ্টা করুন।",
    "no-speech": "কিছু শোনা যায়নি — মাইকের কাছে এসে আরেকটু জোরে বলুন।",
    no_speech: "কিছু শোনা যায়নি — মাইকের কাছে এসে আরেকটু জোরে বলুন।",
    network: "ভয়েস সার্ভারে পৌঁছানো যাচ্ছে না — লিখে কমান্ড দিন।",
    "language-not-supported": "এই ব্রাউজারে বাংলা ভয়েস প্রিভিউ নেই।",
    aborted: "",
  };
  return map[code] ?? "ভয়েস শুনতে সমস্যা হয়েছে — আবার চেষ্টা করুন বা লিখে দিন।";
}

export type ClipAssessment = "ok" | "too_short" | "too_quiet";

/** Quality gate: a clip that cannot contain a command never uploads. */
export function assessClip(durationMs: number, peak: number): ClipAssessment {
  if (durationMs < 500) return "too_short";
  if (peak < 0.01) return "too_quiet";
  return "ok";
}

export type LevelKind = "silent" | "ok" | "clipping";

export function levelKind(level: number): LevelKind {
  if (level < 0.02) return "silent";
  if (level > 0.85) return "clipping";
  return "ok";
}
