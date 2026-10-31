export interface Product {
  id: number; name_bn: string; name_en: string; unit: string;
  price: number; stock: number; low_stock_threshold: number;
}
export interface DraftItem {
  product_id: number; name_bn: string; qty: number; unit: string;
  unit_price: number; line_total: number;
}
export interface DraftAlert {
  type: string;
  product_id?: number | null;
  name_bn?: string;
  qty: number;
  unit: string;
  message_bn: string;
}
export interface Draft {
  kind: string;
  items?: DraftItem[];
  total?: number;
  payment_type?: string;
  customer?: { id: number | null; name: string } | null;
  customer_name?: string;
  unknown_items?: { text: string; qty?: number; stated_price?: number | null }[];
  warnings?: string[];
  alerts?: DraftAlert[];
  alerts_ack?: boolean;
  amount?: number;
  ambiguities?: { text: string; options: { product_id: number; name_bn: string; price: number }[] }[];
  engine?: string;
}
export interface TranscribeResponse {
  text: string;
  alternatives: string[];
  confidence: number | null;
  duration_ms: number;
  warnings: string[];
  engine: string;
}
export interface CommandResponse {
  kind: string;
  draft_id?: number;
  summary_bn?: string;
  answer_bn?: string;
  draft?: Draft;
  needs_clarification?: boolean;
  data?: unknown;
}

const j = async (r: Response) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

export const api = {
  products: (): Promise<Product[]> => fetch("/api/products").then(j),
  customers: () => fetch("/api/customers").then(j),
  summary: (days = 1) => fetch(`/api/reports/summary?days=${days}`).then(j),
  lowStock: (): Promise<Product[]> => fetch("/api/reports/low-stock").then(j),
  command: (text: string): Promise<CommandResponse> =>
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j),
  confirm: (id: number) => fetch(`/api/drafts/${id}/confirm`, { method: "POST" }).then(j),
  reject: (id: number) => fetch(`/api/drafts/${id}/reject`, { method: "POST" }).then(j),
  resolve: (id: number, productId: number, ambiguityIndex?: number) =>
    fetch(`/api/drafts/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productId, ambiguity_index: ambiguityIndex }),
    }).then(j),
  updateItems: (id: number, items: { product_id: number; qty: number }[], acknowledgeUnknown = false) =>
    fetch(`/api/drafts/${id}/items`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, acknowledge_unknown: acknowledgeUnknown }),
    }).then(j),
  daily: (days = 7): Promise<{ date: string; count: number; total: number }[]> =>
    fetch(`/api/reports/daily?days=${days}`).then(j),
  ackAlerts: (id: number) =>
    fetch(`/api/drafts/${id}/alerts/ack`, { method: "POST" }).then(j),
  transcribe: (blob: Blob, filename = "audio.wav"): Promise<TranscribeResponse> => {
    const fd = new FormData();
    // The filename follows the blob's REAL mime type — hardcoding
    // audio.webm broke Safari (audio/mp4) uploads (plan K9).
    fd.append("file", blob, filename);
    return fetch("/api/transcribe", { method: "POST", body: fd }).then(j);
  },
};

export const bnNum = (v: number | string): string =>
  String(v).replace(/[0-9]/g, (d) => "০১২৩৪৫৬৭৮৯"[Number(d)]);
export const taka = (v: number): string => `${bnNum(v)} টাকা`;
