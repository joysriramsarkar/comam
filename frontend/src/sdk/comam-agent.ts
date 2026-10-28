/**
 * Comam Autonomous Agent Engine.
 * 
 * Inspects Bengali user commands and automatically dispatches to the correct tool
 * using the provided ComamPOSAdapter.
 */

import type {
  ComamPOSAdapter,
  POSCustomer,
  POSSaleDraft,
  POSSaleItemDraft,
  POSProductSalesReport,
  POSDueInfo,
} from './types';
import { formatForBengaliSpeech } from './speech-utils';

export interface ComamAgentResponse {
  kind: 'sale' | 'purchase' | 'due_query' | 'report' | 'unknown';
  answerBn: string;
  answerSpeechBn: string;
  draft?: POSSaleDraft | null;
  productReport?: POSProductSalesReport | null;
  dueInfo?: POSDueInfo | null;
  hasUnknownCustomer?: boolean;
  unknownCustomerName?: string;
}

const BENGALI_DIGITS: Record<string, string> = {
  '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
  '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
};

const WORD_NUMBERS: Record<string, number> = {
  'এক': 1, 'দুই': 2, 'তিন': 3, 'চার': 4, 'পাঁচ': 5,
  'ছয়': 6, 'সাত': 7, 'আট': 8, 'নয়': 9, 'দশ': 10,
  'এগারো': 11, 'বারো': 12, 'তেরো': 13, 'চৌদ্দ': 14, 'পনেরো': 15,
  'বিশ': 20, 'ত্রিশ': 30, 'চল্লিশ': 40, 'পঞ্চাশ': 50,
  'দেড়': 1.5, 'আড়াই': 2.5, 'আধা': 0.5,
};

function normalizeBn(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\s\-_]+/g, ' ')
    .replace(/[।,;:!?\"'()]/g, '')
    .trim();
}

function parseNumber(token: string): number | null {
  if (!token) return null;
  const ascii = token.replace(/[০-৯]/g, (d) => BENGALI_DIGITS[d] || d);
  const parsed = parseFloat(ascii);
  if (!isNaN(parsed)) return parsed;
  return WORD_NUMBERS[token] ?? null;
}

function stripCustomerSuffixes(name: string): string {
  let cleaned = name.trim();
  const honorifics = ['-দার', '-দা', '-বাবুর', '-বাবু', '-কাকার', '-কাকা', '-ভাই', '-দিদি'];
  for (const h of honorifics) {
    if (cleaned.endsWith(h) && cleaned.length > h.length + 1) {
      cleaned = cleaned.slice(0, -h.length).trim();
    }
  }
  // Genitive
  if (cleaned.endsWith('-এর')) cleaned = cleaned.slice(0, -3).trim();
  else if (cleaned.endsWith('ের')) cleaned = cleaned.slice(0, -2).trim();
  else if (cleaned.endsWith('ার') && cleaned.length > 3 && !cleaned.endsWith('সরকার')) {
    cleaned = cleaned.slice(0, -1).trim();
  }
  return cleaned;
}

export class ComamAgent {
  private adapter: ComamPOSAdapter;

  constructor(adapter: ComamPOSAdapter) {
    this.adapter = adapter;
  }

  async processCommand(rawText: string): Promise<ComamAgentResponse> {
    const text = rawText.trim();
    if (!text) {
      return {
        kind: 'unknown',
        answerBn: 'দয়া করে কিছু বলুন।',
        answerSpeechBn: 'দয়া করে কিছু বলুন।',
      };
    }

    const norm = normalizeBn(text);

    // 1. Tool: Due query (বাকি হিসাব)
    if (norm.includes('বাকি') && /(?:^|\s)(কত|কাদের|কার|হিসাব)(?:\s|$|[?।,])/.test(norm)) {
      return this.handleDueQuery(text, norm);
    }

    // 2. Tool: Product sales query (আমুল শক্তি কয়টা বিক্রি হয়েছে?)
    if (
      /(?:কয়টা|কয়টা|কত|কটি)/.test(norm) &&
      /(?:বিক্রি|সেল)/.test(norm)
    ) {
      const prodRes = await this.handleProductSalesReport(norm);
      if (prodRes) return prodRes;
    }

    // 3. Tool: Overall sales report (আজ কত বিক্রি হলো?)
    if (
      norm.includes('বিক্রি কেমন') ||
      norm.includes('বিক্রি কত') ||
      (norm.includes('বিক্রি') && norm.includes('আজ'))
    ) {
      return this.handleShopSalesSummary();
    }

    // 4. Tool: Build Billing Sale Draft
    return this.handleSaleBilling(text, norm);
  }

  private async handleDueQuery(raw: string, norm: string): Promise<ComamAgentResponse> {
    const isGeneral = /(?:^|\s)(কার|কাদের|মোট|সবাই|সকলের)(?:\s|$)/.test(norm);
    if (isGeneral) {
      const customers = await this.adapter.getCustomers();
      const withDue = customers.filter((c) => c.currentDue > 0);
      const total = withDue.reduce((sum, c) => sum + c.currentDue, 0);

      const ans = withDue.length === 0
        ? 'বর্তমানে দোকানে কারো বাকি নেই।'
        : `দোকানে মোট বাকি ${total} টাকা। বাকি আছে ${withDue.length} জন ক্রেতার।`;

      return {
        kind: 'due_query',
        answerBn: ans,
        answerSpeechBn: formatForBengaliSpeech(ans),
      };
    }

    const match = raw.match(/([\u0980-\u09FF\w\s]+?)(?:-এর|ের|র)?(?:\s+টাকা)?(?:\s+বাকি\s+কত|\s+কত\s+বাকি|\s+কত\s+টাকা\s+বাকি|\s+বাকি)/);
    const targetNameRaw = match ? match[1].trim() : '';
    const cleanTargetName = stripCustomerSuffixes(targetNameRaw);

    if (cleanTargetName && !/(?:^|\s)(কার|কাদের|মোট|বাকি|কত)(?:\s|$)/.test(cleanTargetName)) {
      const dueInfo = await this.adapter.getCustomerDue(cleanTargetName);
      if (dueInfo) {
        const ans = `${dueInfo.customerName}-এর বর্তমান বাকি ${dueInfo.dueAmount} টাকা।`;
        return {
          kind: 'due_query',
          answerBn: ans,
          answerSpeechBn: formatForBengaliSpeech(ans),
          dueInfo,
        };
      } else {
        const ans = `"${cleanTargetName}" নামে কোনো গ্রাহক খুঁজে পাওয়া যায়নি।`;
        return {
          kind: 'due_query',
          answerBn: ans,
          answerSpeechBn: formatForBengaliSpeech(ans),
          hasUnknownCustomer: true,
          unknownCustomerName: cleanTargetName,
        };
      }
    }

    return {
      kind: 'due_query',
      answerBn: 'নির্দিষ্ট কোনো গ্রাহকের নাম বুঝতে পারিনি।',
      answerSpeechBn: 'নির্দিষ্ট কোনো গ্রাহকের নাম বুঝতে পারিনি।',
    };
  }

  private async handleProductSalesReport(norm: string): Promise<ComamAgentResponse | null> {
    const cleaned = norm
      .replace(/(?:আজকে|আজ|কয়টা|কয়টা|কত|কটি|বিক্রি|হয়েছে|হলো|শেষ|হিসাব|\?|।)/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    if (!cleaned || cleaned.length < 2) return null;

    const matches = await this.adapter.searchProducts(cleaned);
    if (!matches || matches.length === 0) return null;

    const targetProduct = matches[0];
    const report = await this.adapter.getProductSalesReport(targetProduct.id, 1);
    if (!report) return null;

    const ans = `${report.productName} আজ ${report.soldQuantity} ${report.unit} বিক্রি হয়েছে (মোট ${report.salesTotal} টাকা)। বর্তমান স্টক: ${report.currentStock} ${report.unit}।`;

    return {
      kind: 'report',
      answerBn: ans,
      answerSpeechBn: formatForBengaliSpeech(ans),
      productReport: report,
    };
  }

  private async handleShopSalesSummary(): Promise<ComamAgentResponse> {
    if (this.adapter.getShopSalesSummary) {
      const summary = await this.adapter.getShopSalesSummary(1);
      if (summary) {
        let ans = `আজ মোট ${summary.salesTotal} টাকা বিক্রি হয়েছে (${summary.salesCount}টি বিল)।`;
        if (summary.estimatedProfit !== undefined) {
          ans += ` আনুমানিক লাভ ${summary.estimatedProfit} টাকা।`;
        }
        return {
          kind: 'report',
          answerBn: ans,
          answerSpeechBn: formatForBengaliSpeech(ans),
        };
      }
    }

    return {
      kind: 'report',
      answerBn: 'আজকের বিক্রয় সারাংশ হিসাব করা হচ্ছে।',
      answerSpeechBn: 'আজকের বিক্রয় সারাংশ হিসাব করা হচ্ছে।',
    };
  }

  private async handleSaleBilling(raw: string, norm: string): Promise<ComamAgentResponse> {
    const isDue = norm.includes('বাকি') || norm.includes('নামে');
    const isUpi = norm.includes('ইউপিআই') || norm.includes('upi');
    const paymentType = isDue ? 'due' : isUpi ? 'upi' : 'cash';

    let matchedCustomer: POSCustomer | null = null;
    let customerName = '';
    let hasUnknownCustomer = false;

    const custMatch = raw.match(/([—–-]?\s*[\u0980-\u09FF\w\s]+?)(?:-এর|ের|র)?\s*(?:নামে|কে)(?:\s+বাকি)?/);
    if (custMatch) {
      const extracted = custMatch[1].replace(/^[—–-\s]+/, '').trim();
      const stripped = stripCustomerSuffixes(extracted);
      if (stripped && !['নগদ', 'বাকি', 'ইউপিআই'].includes(stripped)) {
        customerName = stripped;
        const allCustomers = await this.adapter.getCustomers();
        const found = allCustomers.find((c) =>
          normalizeBn(c.name) === normalizeBn(stripped) ||
          (c.nameBn && normalizeBn(c.nameBn) === normalizeBn(stripped))
        );

        if (found) {
          matchedCustomer = found;
          customerName = found.nameBn || found.name;
        } else {
          hasUnknownCustomer = true;
        }
      }
    }

    const segments = raw.split(/[,،]| এবং | আর | ও /);
    const itemDrafts: POSSaleItemDraft[] = [];

    for (const seg of segments) {
      const words = seg.trim().split(/\s+/);
      let qty = 1;
      const prodQueryWords: string[] = [];

      for (let i = 0; i < words.length; i++) {
        const num = parseNumber(words[i]);
        if (num !== null && qty === 1 && !words[i].includes('টাকা')) {
          qty = num;
        } else if (!['কেজি', 'গ্রাম', 'প্যাকেট', 'পিস', 'টা', 'টি', 'বিল', 'করো', 'দাও', 'নামে', 'বাকি', 'নগদ'].includes(words[i])) {
          prodQueryWords.push(words[i]);
        }
      }

      const q = prodQueryWords.join(' ').trim();
      if (q) {
        const searchMatches = await this.adapter.searchProducts(q);
        if (searchMatches && searchMatches.length > 0) {
          const prod = searchMatches[0];
          const lineTotal = Math.round(prod.price * qty * 100) / 100;
          itemDrafts.push({
            product: prod,
            quantity: qty,
            unitPrice: prod.price,
            lineTotal,
          });
        }
      }
    }

    const total = itemDrafts.reduce((sum, item) => sum + item.lineTotal, 0);

    const draft: POSSaleDraft = {
      kind: 'sale',
      items: itemDrafts,
      total,
      paymentType,
      customer: matchedCustomer,
      customerName,
      hasUnknownCustomer,
      rawCommand: raw,
    };

    let reply = `মোট বিল ${total} টাকা (${paymentType === 'due' ? 'বাকি' : paymentType === 'upi' ? 'ইউপিআই' : 'নগদ'})।`;
    if (hasUnknownCustomer) {
      reply += ` সতর্কতা: "${customerName}" নামে কোনো গ্রাহক নিবন্ধিত নেই।`;
    }

    return {
      kind: 'sale',
      answerBn: reply,
      answerSpeechBn: formatForBengaliSpeech(reply),
      draft,
      hasUnknownCustomer,
      unknownCustomerName: customerName,
    };
  }
}
