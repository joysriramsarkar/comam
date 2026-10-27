import { useCallback, useEffect, useRef, useState } from "react";
import { api, bnNum, taka, type CommandResponse, type DraftItem, type Product } from "./api";

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
  const [listening, setListening] = useState(false);
  const recogRef = useRef<unknown>(null);
  const [daily, setDaily] = useState<{ date: string; count: number; total: number }[]>([]);
  const [editItems, setEditItems] = useState<DraftItem[]>([]);

  const refresh = useCallback(async () => {
    const [p, c, s, d] = await Promise.all([api.products(), api.customers(), api.summary(1), api.daily(7)]);
    setProducts(p); setCustomers(c); setSummary(s); setDaily(d);
  }, []);

  useEffect(() => {
    if (resp?.draft?.items) setEditItems(resp.draft.items.map((i) => ({ ...i })));
  }, [resp?.draft_id, resp?.draft]);

  useEffect(() => { refresh().catch(() => setError("সার্ভারে সংযোগ যাচ্ছে না")); }, [refresh]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true); setError("");
    try {
      const r = await api.command(text.trim());
      setResp(r);
      if (r.kind === "report" || r.kind === "due_query") setText("");
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const confirm = async () => {
    if (!resp?.draft_id) return;
    setBusy(true);
    try {
      await api.updateItems(resp.draft_id, editItems.map((i) => ({ product_id: i.product_id, qty: i.qty })));
      await api.confirm(resp.draft_id);
      setResp(null); setText(""); await refresh();
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const resolveDraft = async (productId: number) => {
    if (!resp?.draft_id) return;
    setBusy(true);
    try {
      const r = await api.resolve(resp.draft_id, productId);
      setResp({ ...resp, draft: r.draft, summary_bn: r.summary_bn, needs_clarification: false });
    } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const setQty = (idx: number, qty: number) =>
    setEditItems((items) => items.map((it, i) => (i === idx ? { ...it, qty: Math.max(0, qty) } : it)));

  const reject = async () => {
    if (resp?.draft_id) await api.reject(resp.draft_id).catch(() => {});
    setResp(null);
  };

  const mic = () => {
    const SR = (window as unknown as { webkitSpeechRecognition?: unknown; SpeechRecognition?: unknown });
    const Ctor = (SR.SpeechRecognition || SR.webkitSpeechRecognition) as
      (new () => { lang: string; onresult: (e: { results: { [k: number]: { [k: number]: { transcript: string } } } }) => void; onend: () => void; start: () => void }) | undefined;
    if (!Ctor) { setError("এই ব্রাউজারে voice recognition নেই — টাইপ করে বলুন, অথবা সার্ভারের transcribe ব্যবহার হবে"); return; }
    const rec = new Ctor();
    rec.lang = "bn-BD";
    rec.onresult = (e) => setText(e.results[0][0].transcript);
    rec.onend = () => setListening(false);
    recogRef.current = rec;
    setListening(true);
    rec.start();
  };

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
          <button className="mic" onClick={mic} disabled={listening}>{listening ? "শুনছি…" : "🎤 বলুন"}</button>
          <button className="primary" onClick={send} disabled={busy || !text.trim()}>পাঠান</button>
        </div>
        {error && <p className="err">{error}</p>}
      </div>

      {resp?.answer_bn && <div className="answer">{resp.answer_bn}</div>}

      {resp?.draft_id && resp.draft && !resp.needs_clarification && (
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
          <div className="row">
            <button className="primary" onClick={confirm} disabled={busy}>✓ ঠিক আছে, সেভ করুন</button>
            <button onClick={reject}>বাতিল</button>
          </div>
        </div>
      )}

      {resp?.needs_clarification && resp.draft?.ambiguities?.map((a, idx) => (
        <div className="amb" key={idx}>
          <div>কোনটা বলছেন?</div>
          <div className="row">
            {a.options.map((o) => (
              <button key={o.product_id} onClick={() => resolveDraft(o.product_id)} disabled={busy}>
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
