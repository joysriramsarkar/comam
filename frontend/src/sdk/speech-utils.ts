/**
 * Bengali Speech Synthesis & Audio Formatting utilities for Comam.
 */

export function formatForBengaliSpeech(text: string): string {
  if (!text) return '';

  let formatted = text;

  // 1. Convert decimal numbers (e.g. 1023.5 or ১০২৩.৫) to "X দশমিক Y"
  formatted = formatted.replace(
    /([০-৯]+|\d+)\.([০-৯]+|\d+)/g,
    (_, intPart, decPart) => `${intPart} দশমিক ${decPart}`
  );

  // 2. Convert currency symbols to Bengali words
  formatted = formatted.replace(/[৳₹]\s*([০-৯]+|\d+)/g, '$1 টাকা');
  formatted = formatted.replace(/[৳₹]/g, ' টাকা ');

  // 3. Convert multiplication symbol ×
  formatted = formatted.replace(/×/g, ' গুণ ');

  // 4. Strip markdown formatting
  formatted = formatted.replace(/[*#_`~]/g, ' ');

  // 5. Replace trailing sentence periods with Bengali danda so the speech synthesizer pauses naturally
  formatted = formatted.replace(/\.(?=\s|$)/g, '।');

  // 6. Clean whitespace
  return formatted.replace(/\s+/g, ' ').trim();
}

// Chrome returns an EMPTY voice list on the first getVoices() call; the
// real list arrives with 'voiceschanged'. Cache + listen once, so the
// Bengali voice is actually found instead of silently falling back to
// the default (non-Bengali) voice (MASTER-PLAN MP-15).
let cachedVoices: SpeechSynthesisVoice[] = [];
if (typeof window !== "undefined" && "speechSynthesis" in window) {
  const load = () => {
    cachedVoices = window.speechSynthesis.getVoices();
  };
  load();
  window.speechSynthesis.addEventListener?.("voiceschanged", load);
}

export function bengaliVoice(): SpeechSynthesisVoice | undefined {
  const voices = cachedVoices.length
    ? cachedVoices
    : typeof window !== "undefined" && "speechSynthesis" in window
      ? window.speechSynthesis.getVoices()
      : [];
  return (
    voices.find((v) => (v.lang === "bn-BD" || v.lang === "bn-IN") && /natural|online/i.test(v.name)) ||
    voices.find((v) => v.lang === "bn-BD" || v.lang === "bn-IN" || v.lang.startsWith("bn")) ||
    voices.find((v) => /bengali|bangla/i.test(v.name))
  );
}

/**
 * Speak text in natural Bengali using Web Speech API with calibrated voice and rate.
 */
export function speakBengali(
  text: string,
  options?: {
    rate?: number;
    pitch?: number;
    onEnd?: () => void;
    onError?: (err: unknown) => void;
  }
): void {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    return;
  }

  try {
    window.speechSynthesis.cancel();

    const formattedText = formatForBengaliSpeech(text);
    const utterance = new SpeechSynthesisUtterance(formattedText);
    utterance.lang = 'bn-BD';
    utterance.rate = options?.rate ?? 0.94;
    utterance.pitch = options?.pitch ?? 1.0;

    const bnVoice = bengaliVoice();
    if (bnVoice) {
      utterance.voice = bnVoice;
    }

    if (options?.onEnd) utterance.onend = options.onEnd;
    if (options?.onError) utterance.onerror = (e) => options?.onError?.(e);

    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.warn('[Comam Speech] TTS Error:', err);
    options?.onError?.(err);
  }
}

export function stopSpeech(): void {
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
}
