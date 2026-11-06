/**
 * Parse a spoken/typed answer to "সব ঠিক আছে, বিল করবো?" (or the
 * due-payment variant) into a safe intent.
 *
 * NO is checked BEFORE YES: the Bengali refusal "না, বাতিল করো" contains
 * "করো", which a naive yes-first check would confirm — and confirming
 * writes money. Anything unrecognised is null (keep the draft pending,
 * never bill), and the tie always breaks toward NOT billing.
 */
export type ConfirmIntent = "yes" | "no" | null;

const NO_RE =
  /(না|বাতিল|থাক|বন্ধ|\bcancel\b|\bno\b|\bnope\b|\bstop\b)/i;

const YES_RE =
  /(হ্যাঁ|হ্যা|হাঁ|ঠিক\s*আছে|ঠিকাছে|বিল\s*কর|সেভ\s*কর|জমা\s*লিখে|জমা\s*নাও|জমা\s*কর|করে\s*দাও|করো|করুন|\byes\b|\byeah\b|\bok\b|\bokay\b)/i;

export function confirmIntent(raw: string): ConfirmIntent {
  const t = (raw || "").toLowerCase().replace(/\s+/g, " ").trim();
  if (!t) return null;
  if (NO_RE.test(t)) return "no";
  if (YES_RE.test(t)) return "yes";
  return null;
}
