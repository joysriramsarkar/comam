import React, { useState, useEffect, useRef, useCallback } from 'react';
import type { ComamPOSAdapter, POSSaleDraft, POSSaleItemDraft } from './types';
import { ComamAgent, type ComamAgentResponse } from './comam-agent';
import { speakBengali, stopSpeech } from './speech-utils';
import { useVoiceInput } from './useVoiceInput';

export interface ComamVoiceWidgetProps {
  adapter: ComamPOSAdapter;
  onSaleSuccess?: (result: any) => void;
  position?: 'bottom-right' | 'bottom-left';
}

export function ComamVoiceWidget({
  adapter,
  onSaleSuccess,
  position = 'bottom-right',
}: ComamVoiceWidgetProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [inputText, setInputText] = useState<string>('');
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [agentResponse, setAgentResponse] = useState<ComamAgentResponse | null>(null);
  const [isCreatingCustomer, setIsCreatingCustomer] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const agentRef = useRef<ComamAgent>(new ComamAgent(adapter));

  // The one shared voice path (MP-10/MP-11): record -> adapter's server
  // ASR when provided; browser speech only as the degraded fallback.
  // The transcript lands in the input box for the shopkeeper to see —
  // it is processed only when they press send (no more first-final
  // auto-submit cutting a bill in half).
  const voice = useVoiceInput({
    transcribe: adapter.transcribeAudio
      ? async (blob, filename) => {
          const r = await adapter.transcribeAudio!(blob, filename);
          return typeof r === 'string' ? { text: r } : r;
        }
      : undefined,
    onTranscript: (t) => {
      setInputText(t);
      setErrorMsg(null);
    },
    onError: (m) => setErrorMsg(m),
  });
  const isListening = voice.isListening;
  const startListening = voice.start;
  const stopListening = voice.stop;

  // Update agent if adapter instance changes
  useEffect(() => {
    agentRef.current = new ComamAgent(adapter);
  }, [adapter]);

  // Clean state when modal opens
  const resetState = useCallback(() => {
    setInputText('');
    setAgentResponse(null);
    setErrorMsg(null);
    stopSpeech();
  }, []);

  const handleProcessText = useCallback(
    async (textToProcess: string) => {
      const q = textToProcess.trim();
      if (!q) return;

      setIsProcessing(true);
      setErrorMsg(null);

      try {
        const response = await agentRef.current.processCommand(q);
        setAgentResponse(response);

        // Speak the natural Bengali response (with "দশমিক", no "পুঁটকি")
        speakBengali(response.answerSpeechBn || response.answerBn);
      } catch (err: any) {
        console.error('[Comam Widget] Agent error:', err);
        setErrorMsg('অনুরোধ প্রক্রিয়া করতে সমস্যা হয়েছে।');
      } finally {
        setIsProcessing(false);
      }
    },
    []
  );

  // Keyboard shortcut Alt+V to open the widget (voice starts only on
  // an explicit mic press — no auto-start; see MASTER-PLAN §4.1.2)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'v' || e.key === 'V')) {
        e.preventDefault();
        setIsOpen((prev: boolean) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Opening the modal only resets state. Recording starts on an
  // explicit mic press: the old 250 ms auto-start fired while the
  // permission dialog / TTS tail was still in the air and died with
  // a silent no-speech (MASTER-PLAN MP-11).
  useEffect(() => {
    if (isOpen) {
      resetState();
    } else {
      stopSpeech();
      stopListening();
    }
  }, [isOpen, resetState, stopListening]);

  // 1-Click Create Customer handler
  const handleCreateCustomer = async () => {
    const name = agentResponse?.unknownCustomerName;
    if (!name) return;

    setIsCreatingCustomer(true);
    try {
      const newCustomer = await adapter.createCustomer(name);
      if (agentResponse?.draft) {
        setAgentResponse({
          ...agentResponse,
          hasUnknownCustomer: false,
          draft: {
            ...agentResponse.draft,
            customer: newCustomer,
            hasUnknownCustomer: false,
            customerName: newCustomer.nameBn || newCustomer.name,
          },
        });
      }
      speakBengali(`"${name}" গ্রাহক হিসেবে সফলভাবে যুক্ত হয়েছে।`);
    } catch (err) {
      console.error('[Comam Widget] Failed to create customer:', err);
      setErrorMsg('গ্রাহক তৈরি করা সম্ভব হয়নি।');
    } finally {
      setIsCreatingCustomer(false);
    }
  };

  // Add draft items to cart or commit
  const handleCommitSale = async () => {
    if (!agentResponse?.draft) return;

    setIsProcessing(true);
    try {
      if (adapter.commitSale) {
        const res = await adapter.commitSale(agentResponse.draft);
        if (res.success) {
          speakBengali('বিল সফলভাবে সম্পন্ন হয়েছে।');
          onSaleSuccess?.(res);
          setIsOpen(false);
        } else {
          setErrorMsg(res.error || 'বিল সম্পন্ন করা যায়নি।');
        }
      } else if (adapter.addToCart) {
        await adapter.addToCart(agentResponse.draft);
        speakBengali('পণ্যগুলো কার্টে যোগ করা হয়েছে।');
        setIsOpen(false);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'বিল তৈরিতে সমস্যা হয়েছে।');
    } finally {
      setIsProcessing(false);
    }
  };

  const posStyle: React.CSSProperties =
    position === 'bottom-left'
      ? { position: 'fixed', bottom: '24px', left: '24px', zIndex: 50 }
      : { position: 'fixed', bottom: '24px', right: '24px', zIndex: 50 };

  return (
    <>
      {/* Floating Microphone Trigger */}
      <div style={posStyle}>
        <button
          id="comam-mic-trigger"
          onClick={() => setIsOpen(true)}
          title="comam AI ভয়েস বিলিং (Alt+V)"
          style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            backgroundColor: '#059669',
            color: '#ffffff',
            boxShadow: '0 8px 24px rgba(5, 150, 105, 0.4)',
            border: 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            transition: 'transform 0.2s, background-color 0.2s',
          }}
          onMouseEnter={(e: React.MouseEvent<HTMLButtonElement>) => (e.currentTarget.style.transform = 'scale(1.08)')}
          onMouseLeave={(e: React.MouseEvent<HTMLButtonElement>) => (e.currentTarget.style.transform = 'scale(1)')}
        >
          <svg width="26" height="26" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
            />
          </svg>
        </button>
      </div>

      {/* Modal Dialog */}
      {isOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9999,
            backgroundColor: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
          onClick={(e: React.MouseEvent<HTMLDivElement>) => {
            if (e.target === e.currentTarget) setIsOpen(false);
          }}
        >
          <div
            style={{
              backgroundColor: '#ffffff',
              borderRadius: '20px',
              maxWidth: '520px',
              width: '100%',
              boxShadow: '0 20px 40px rgba(0,0,0,0.2)',
              overflow: 'hidden',
              fontFamily: 'system-ui, -apple-system, sans-serif',
            }}
          >
            {/* Header */}
            <div
              style={{
                background: 'linear-gradient(135deg, #ecfdf5 0%, #f0fdf4 100%)',
                padding: '18px 20px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                borderBottom: '1px solid #d1fae5',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '10px',
                    backgroundColor: '#059669',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#ffffff',
                    fontWeight: 'bold',
                  }}
                >
                  ⚡
                </div>
                <div>
                  <div style={{ fontWeight: '700', fontSize: '17px', color: '#065f46' }}>
                    comam <span style={{ fontSize: '11px', background: '#d1fae5', padding: '2px 6px', borderRadius: '4px' }}>AI</span>
                  </div>
                  <div style={{ fontSize: '12px', color: '#047857' }}>
                    বাংলায় মুখে বলুন — স্বয়ংক্রিয়ভাবে বিল ও হিসাব প্রস্তুত হবে
                  </div>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  fontSize: '22px',
                  color: '#6b7280',
                  cursor: 'pointer',
                  lineHeight: 1,
                }}
              >
                &times;
              </button>
            </div>

            {/* Body */}
            <div style={{ padding: '20px' }}>
              {/* Input Area */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  backgroundColor: '#f9fafb',
                  border: isListening ? '2px solid #059669' : '1px solid #e5e7eb',
                  borderRadius: '12px',
                  padding: '8px 12px',
                  transition: 'border 0.2s',
                }}
              >
                <input
                  type="text"
                  value={inputText}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
                  onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                    if (e.key === 'Enter') handleProcessText(inputText);
                  }}
                  placeholder={isListening ? 'শুনছি... বলুন...' : 'মুখে বলুন বা বাংলায় লিখুন (যেমন: ২ কেজি চিনি...)'}
                  style={{
                    flex: 1,
                    border: 'none',
                    background: 'transparent',
                    outline: 'none',
                    fontSize: '15px',
                    color: '#1f2937',
                  }}
                />
                {inputText && (
                  <button
                    onClick={() => setInputText('')}
                    style={{ background: 'none', border: 'none', color: '#9ca3af', cursor: 'pointer', fontSize: '16px' }}
                  >
                    ✕
                  </button>
                )}
                <button
                  onClick={isListening ? stopListening : startListening}
                  style={{
                    backgroundColor: isListening ? '#dc2626' : '#059669',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '8px',
                    width: '38px',
                    height: '38px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    transition: 'background-color 0.2s',
                  }}
                  title={isListening ? 'থামান' : 'ভয়েস রেকর্ড শুরু করুন'}
                >
                  <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="2"
                      d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                    />
                  </svg>
                </button>
                <button
                  onClick={() => handleProcessText(inputText)}
                  disabled={!inputText.trim() || isProcessing}
                  style={{
                    backgroundColor: '#10b981',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '8px',
                    width: '38px',
                    height: '38px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: !inputText.trim() || isProcessing ? 'not-allowed' : 'pointer',
                    opacity: !inputText.trim() || isProcessing ? 0.6 : 1,
                  }}
                >
                  ➤
                </button>
              </div>

              {/* Listening indicator */}
              {isListening && (
                <div style={{ marginTop: '10px', color: '#059669', fontSize: '13px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span
                      style={{
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        backgroundColor: '#ef4444',
                        display: 'inline-block',
                      }}
                    />
                    রেকর্ড হচ্ছে... স্পষ্ট করে বলুন, শেষ হলে আবার মাইক বোতাম চাপুন
                  </div>
                  <div style={{ height: '6px', backgroundColor: '#e5e7eb', borderRadius: '3px', marginTop: '6px', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.round(voice.level * 100)}%`, backgroundColor: '#059669' }} />
                  </div>
                  {voice.levelKind === 'silent' && (
                    <div style={{ color: '#b45309', marginTop: '4px' }}>মাইক আওয়াজ পাচ্ছে না — কাছে এসে বলুন</div>
                  )}
                  {voice.interimText && (
                    <div style={{ color: '#6b7280', marginTop: '4px' }}>শুনছি: {voice.interimText}</div>
                  )}
                </div>
              )}
              {voice.isProcessing && (
                <div style={{ marginTop: '10px', color: '#059669', fontSize: '13px' }}>
                  ট্রান্সক্রাইব হচ্ছে...
                </div>
              )}
              {voice.degraded && inputText && (
                <div style={{ marginTop: '10px', color: '#b45309', fontSize: '13px' }}>
                  ব্রাউজারের ভয়েসে শুনেছি — লেখাটা ঠিক আছে কিনা দেখে তারপর ➤ পাঠান।
                </div>
              )}

              {/* Error Message */}
              {errorMsg && (
                <div
                  style={{
                    marginTop: '12px',
                    backgroundColor: '#fef2f2',
                    color: '#b91c1c',
                    padding: '10px 14px',
                    borderRadius: '8px',
                    fontSize: '13px',
                    border: '1px solid #fecaca',
                  }}
                >
                  {errorMsg}
                </div>
              )}

              {/* Agent Answer View */}
              {agentResponse && !agentResponse.draft && (
                <div
                  style={{
                    marginTop: '16px',
                    backgroundColor: '#ecfdf5',
                    border: '1px solid #a7f3d0',
                    borderRadius: '12px',
                    padding: '14px',
                  }}
                >
                  <div style={{ fontWeight: '600', color: '#065f46', marginBottom: '4px', fontSize: '14px' }}>
                    ✓ হিসাব তথ্য:
                  </div>
                  <div style={{ color: '#047857', fontSize: '14px', lineHeight: 1.5 }}>
                    {agentResponse.answerBn}
                  </div>
                </div>
              )}

              {/* Unknown Customer Warning & 1-Click Create */}
              {agentResponse?.hasUnknownCustomer && agentResponse.unknownCustomerName && (
                <div
                  style={{
                    marginTop: '14px',
                    backgroundColor: '#fffbeb',
                    border: '1px solid #fde68a',
                    borderRadius: '12px',
                    padding: '14px',
                  }}
                >
                  <div style={{ fontWeight: '600', color: '#92400e', fontSize: '13px', marginBottom: '4px' }}>
                    ⚠️ গ্রাহক নিবন্ধিত নেই
                  </div>
                  <div style={{ color: '#b45309', fontSize: '13px', marginBottom: '10px' }}>
                    &ldquo;{agentResponse.unknownCustomerName}&rdquo; নামে কোনো গ্রাহক পাওয়া যায়নি।
                  </div>
                  <button
                    onClick={handleCreateCustomer}
                    disabled={isCreatingCustomer}
                    style={{
                      backgroundColor: '#d97706',
                      color: '#ffffff',
                      border: 'none',
                      borderRadius: '8px',
                      padding: '8px 14px',
                      fontSize: '13px',
                      fontWeight: '600',
                      cursor: isCreatingCustomer ? 'not-allowed' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    {isCreatingCustomer ? 'তৈরি হচ্ছে...' : `+ "${agentResponse.unknownCustomerName}" নামে নতুন গ্রাহক তৈরি করুন`}
                  </button>
                </div>
              )}

              {/* Sale Draft Details */}
              {agentResponse?.draft && agentResponse.draft.items.length > 0 && (
                <div
                  style={{
                    marginTop: '16px',
                    border: '1px solid #e5e7eb',
                    borderRadius: '12px',
                    padding: '14px',
                    backgroundColor: '#ffffff',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                    <span style={{ fontWeight: '600', color: '#1f2937', fontSize: '14px' }}>
                      🛒 প্রস্তুতকৃত বিল খসড়া
                    </span>
                    <span
                      style={{
                        fontSize: '12px',
                        padding: '2px 8px',
                        borderRadius: '6px',
                        backgroundColor: agentResponse.draft.paymentType === 'due' ? '#fee2e2' : '#ecfdf5',
                        color: agentResponse.draft.paymentType === 'due' ? '#991b1b' : '#065f46',
                        fontWeight: '600',
                      }}
                    >
                      {agentResponse.draft.paymentType === 'due' ? 'বাকি' : agentResponse.draft.paymentType === 'upi' ? 'ইউপিআই' : 'নগদ'}
                    </span>
                  </div>

                  {/* Item List */}
                  <div style={{ maxHeight: '160px', overflowY: 'auto' }}>
                    {agentResponse.draft.items.map((it: POSSaleItemDraft, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          padding: '6px 0',
                          borderBottom: '1px dashed #f3f4f6',
                          fontSize: '13px',
                        }}
                      >
                        <div>
                          <span style={{ fontWeight: '500', color: '#374151' }}>
                            {it.product.nameBn || it.product.name}
                          </span>
                          <span style={{ color: '#6b7280', marginLeft: '6px' }}>
                            ({it.quantity} {it.product.unit} × {it.unitPrice} টাকা)
                          </span>
                        </div>
                        <span style={{ fontWeight: '600', color: '#111827' }}>
                          {it.lineTotal} টাকা
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Total & Action */}
                  <div
                    style={{
                      marginTop: '12px',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      paddingTop: '10px',
                      borderTop: '2px solid #f3f4f6',
                    }}
                  >
                    <span style={{ fontWeight: '700', fontSize: '16px', color: '#111827' }}>
                      সর্বমোট: {agentResponse.draft.total} টাকা
                    </span>
                    <button
                      onClick={handleCommitSale}
                      disabled={isProcessing || (agentResponse.draft.paymentType === 'due' && agentResponse.draft.hasUnknownCustomer)}
                      style={{
                        backgroundColor: '#059669',
                        color: '#ffffff',
                        border: 'none',
                        borderRadius: '8px',
                        padding: '9px 18px',
                        fontSize: '14px',
                        fontWeight: '600',
                        cursor: 'pointer',
                        transition: 'background-color 0.2s',
                        opacity: isProcessing ? 0.7 : 1,
                      }}
                    >
                      {adapter.commitSale ? 'সরাসরি বিল করুন ➔' : 'কার্টে যোগ করুন ➔'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
