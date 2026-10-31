import { useCallback, useEffect, useState } from "react";
import { api, bnNum, taka, type CommandResponse, type DraftItem, type Product } from "./api";
import { useVoiceInput } from "./sdk/useVoiceInput";

interface Summary { sales_total: number; sales_count: number; est_profit: number }
interface Customer { id: number; name: string; due: number }

export default function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [text, setText] = useState("");
  const [resp, setResp] = useState<CommandResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [heardText, setHeardText] = useState("");
  const [itemMode, setItemMode] = useState(false);
  const [daily, setDaily] = useState<{ date: string; count: number; total: number }[]>([]);
  const [editItems, setEditItems] = useState<DraftItem[]>([]);
  const [unknownAck, setUnknownAck] = useState(false);

  // One shared voice path (MP-10/MP-12): record -> server ASR is the
  // only final text; Web Speech appears only as a live preview caption.
  const voice = useVoiceInput({
    transcribe: (blob, filename) => api.transcribe(blob, filename),
    enablePreview: true,
    onTranscript: (t) => {
      setError("");
      // Item-by-item mode (MP-26): each item is its own short clip,
      // accumulated into one command — sentence-boundary garbling
      // cannot take the whole bill down with it.
      setText((prev) => (itemMode && prev.trim() ? `${prev.replace(/[।,]?\s*$/, "")}, ${t}` : t));
    },
    onError: (m) => setError(m),
  });

  const refresh = useCallback(async () => {
    const [p, c, s, d] = await Promise.all([api.products(), api.customers(), api.summary(1), api.daily(7)]);
    setProducts(p); setCustomers(c); setSummary(s); setDaily(d);
  }, []);

  useEffect(() => {
    if (resp?.draft?.items) setEditItems(resp.draft.items.map((i) => ({ ...i })));
    setUnknownAck(false);
  }, [resp?.draft_id, resp?.draft]);

  useEffect(() => { refresh().catch(() => setError("সার্ভারে সংযোগ যাচ্ছে না")); }, [refresh]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true); setError("");
    setHeardText(text.trim()); // transcript read-back snapshot (MP-25)
    try {
      const r = await api.command(text.trim());
      setResp(r);
      if (r.kind === "report" || r.kind === "due_query") setText("");
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const ackAlerts = async () => {
    if (!resp?.draft_id) return;
    setBusy(true);
    try {
      const r = await api.ackAlerts(resp.draft_id);
      setResp({
        ...resp,
        draft: r.draft,
        summary_bn: r.summary_bn,
        needs_clarification:
          (r.draft?.ambiguities?.length ?? 0) > 0 ||
          !!(r.draft?.alerts?.length && !r.draft?.alerts_ack),
      });
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const confirm = async () => {
    if (!resp?.draft_id) return;
    setBusy(true);
    try {
      // Only sale/purchase drafts have editable items — due payments and
      // undos confirm directly (the items endpoint rejects other kinds).
      if (resp.kind === "sale" || resp.kind === "purchase") {
        await api.updateItems(resp.draft_id, editItems.map((i) => ({ product_id: i.product_id, qty: i.qty })), unknownAck);
      }
      await api.confirm(resp.draft_id);
      setResp(null); setText(""); await refresh();
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const resolveDraft = async (productId: number, ambiguityIndex: number) => {
    if (!resp?.draft_id) return;
    setBusy(true);
    try {
      const r = await api.resolve(resp.draft_id, productId, ambiguityIndex);
      setResp({
        ...resp,
        draft: r.draft,
        summary_bn: r.summary_bn,
        needs_clarification:
          (r.draft?.ambiguities?.length ?? 0) > 0 ||
          !!(r.draft?.alerts?.length && !r.draft?.alerts_ack),
      });
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const setQty = (idx: number, qty: number) =>
    setEditItems((items) => items.map((it, i) => (i === idx ? { ...it, qty: Math.max(0, qty) } : it)));

  const reject = async () => {
    if (resp?.draft_id) await api.reject(resp.draft_id).catch(() => {});
    setResp(null);
  };

  const ambPending = (resp?.draft?.ambiguities?.length ?? 0) > 0;
  const alertPending = !!(resp?.draft?.alerts?.length && !resp.draft?.alerts_ack);

  const lowCount = products.filter((p) => p.stock <= p.low_stock_threshold).length;
  const totalDue = customers.reduce((a, c) => a + (c.due || 0), 0);

  return (
    <div className="wrap">
      <header>
        <h1>comam <span>AI</span></h1>
        <p className="tagline">বললেই হিসাব — বাংলা ভয়েসে দোকান চালান</p>
      </header>

      <div className="cards">
        <div className="card"><div className="k">আজকের বিক্রি</div><div className="v">{summary ? taka(summary.sales_total) : "…"}</div></div>
        <div className="card"><div className="k">মোট বাকি</div><div className="v">{taka(totalDue)}</div></div>
        <div className="card"><div className="k">কম স্টক</div><div className="v">{bnNum(lowCount)}টা পণ্য</div></div>
      </div>

      <div className="cmdbox">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="যেমন: দুই কেজি চিনি, তিনটে সাবান, রহিমদার নামে বাকিতে"
        />
        <div className="row">
          <button className="mic" onClick={voice.toggle} disabled={voice.isProcessing}>
            {voice.isListening
              ? `⏺ শুনছি… থামাতে চাপুন (${bnNum(voice.elapsedSec)}সে)`
              : voice.isProcessing
                ? "ট্রান্সক্রাইব হচ্ছে…"
                : "🎤 বলুন"}
          </button>
          <button className="primary" onClick={send} disabled={busy || !text.trim()}>পাঠান</button>
        </div>
        {voice.isListening && (
          <div className="meter" title="মাইকের শব্দের মাত্রা">
            <i style={{ width: `${Math.round(voice.level * 100)}%` }} />
          </div>
        )}
        {voice.isListening && voice.levelKind === "silent" && (
          <p className="warnote">মাইক আওয়াজ পাচ্ছে না — কাছে এসে বলুন</p>
        )}
        {voice.isListening && voice.levelKind === "clipping" && (
          <p className="warnote">শব্দ খুব বেশি — একটু দূর থেকে বলুন</p>
        )}
        {voice.interimText && (
          <p className="interim">শুনছি: {voice.interimText}</p>
        )}
        <div className="row">
          <label className="ackrow">
            <input type="checkbox" checked={itemMode} onChange={(e) => setItemMode(e.target.checked)} />
            একটা করে আইটেম বলব
          </label>
          {voice.devices.length > 1 && (
            <select
              value={voice.deviceId}
              onChange={(e) => voice.setDeviceId(e.target.value)}
              title="মাইক্রোফোন বাছাই"
            >
              <option value="">ডিফল্ট মাইক</option>
              {voice.devices.map((d, i) => (
                <option key={d.deviceId || i} value={d.deviceId}>
                  {d.label || `মাইক ${bnNum(i + 1)}`}
                </option>
              ))}
            </select>
          )}
          {voice.lastAudioUrl && (
            <audio controls src={voice.lastAudioUrl} style={{ height: 28 }} />
          )}
        </div>
        {error && <p className="err">{error}</p>}
      </div>

      {resp?.answer_bn && <div className="answer">{resp.answer_bn}</div>}
      {resp && heardText && (
        <div className="answer">আমি শুনলাম: <b>{heardText}</b></div>
      )}

      {resp?.draft_id && resp.draft && !ambPending && (
        <div className="draft">
          <div className="sum">{resp.summary_bn}</div>
          <ul className="editlist">
            {editItems.map((i, idx) => (
              <li key={i.product_id}>
                <span>{i.name_bn} — {taka(i.unit_price)}/{i.unit}</span>
                <span className="qtyctl">
                  <button onClick={() => setQty(idx, +(i.qty - (i.unit === "piece" ? 1 : 0.5)).toFixed(2))}>−</button>
                  <b>{bnNum(i.qty)}</b>
                  <button onClick={() => setQty(idx, +(i.qty + (i.unit === "piece" ? 1 : 0.5)).toFixed(2))}>+</button>
                  <button onClick={() => setQty(idx, 0)}>✕</button>
                </span>
                <span>{taka(i.unit_price * i.qty)}</span>
              </li>
            ))}
          </ul>
          <div className="sum">মোট {taka(editItems.reduce((a, i) => a + i.unit_price * i.qty, 0))}</div>
          {!!resp.draft.unknown_items?.length && (
            <>
              <p className="err">
                ⚠ ক্যাটালগে নেই: {resp.draft.unknown_items.map((u) => u.text).join(", ")}
                {" "}— এই পণ্য ক্যাটালগে না থাকায় বিলে ধরা হয়নি।
              </p>
              <label className="ackrow">
                <input type="checkbox" checked={unknownAck} onChange={(e) => setUnknownAck(e.target.checked)} />
                ক্যাটালগে নেই এমন পণ্য বাদ দিয়েই সেভ করুন
              </label>
            </>
          )}
          {!!resp.draft.warnings?.length && (
            <p className="warnote">⚠ {resp.draft.warnings.join("; ")}</p>
          )}
          {alertPending && (
            <div className="amb">
              <div>⚠ পরিমাণ যাচাই করুন:</div>
              {resp.draft.alerts!.map((a, i) => (
                <p key={i} className="warnote">{a.message_bn}</p>
              ))}
              <div className="row">
                <button className="primary" onClick={ackAlerts} disabled={busy}>
                  হ্যাঁ, পরিমাণ ঠিক আছে
                </button>
                <button onClick={reject}>বাতিল করে আবার বলুন</button>
              </div>
            </div>
          )}
          <div className="row">
            <button className="primary" onClick={confirm} disabled={busy || alertPending || (!!resp.draft.unknown_items?.length && !unknownAck)}>✓ ঠিক আছে, সেভ করুন</button>
            <button onClick={reject}>বাতিল</button>
          </div>
        </div>
      )}

      {resp?.needs_clarification && resp.draft?.ambiguities?.map((a, idx) => (
        <div className="amb" key={idx}>
          <div>কোনটা বলছেন?</div>
          <div className="row">
            {a.options.map((o) => (
              <button key={o.product_id} onClick={() => resolveDraft(o.product_id, idx)} disabled={busy}>
                {o.name_bn} ({taka(o.price)})
              </button>
            ))}
          </div>
        </div>
      ))}

      <h2>পণ্য ও স্টক</h2>
      <table>
        <thead><tr><th>পণ্য</th><th>দাম</th><th>স্টক</th></tr></thead>
        <tbody>
          {products.map((p) => (
            <tr key={p.id}>
              <td>{p.name_bn}</td><td>{taka(p.price)}</td>
              <td className={p.stock <= p.low_stock_threshold ? "low" : ""}>{bnNum(p.stock)} {p.unit}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>গত ৭ দিনের বিক্রি</h2>
      <div className="card">
        <div className="bars">
          {daily.map((d) => (
            <div key={d.date} className="bar" style={{ height: `${Math.max(3, (d.total / Math.max(1, ...daily.map((x) => x.total))) * 100)}%` }}>
              <small>{taka(d.total)}</small>
            </div>
          ))}
        </div>
        <div className="barlabels">
          {daily.map((d) => <span key={d.date}>{bnNum(d.date.slice(8))}/{bnNum(d.date.slice(5, 7))}</span>)}
        </div>
      </div>

      <h2>বাকি খাতা</h2>
      <table>
        <thead><tr><th>ক্রেতা</th><th>বাকি</th></tr></thead>
        <tbody>
          {customers.map((c) => (
            <tr key={c.id}><td>{c.name}</td><td>{taka(c.due || 0)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
