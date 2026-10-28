/**
 * Comam POS Adapter Specification (Contract).
 * 
 * Any POS application implements this interface to connect to Comam.
 * The POS application does NOT need to know any Bengali grammar,
 * speech synthesis rules, regexes, or intent classification.
 */

export interface POSProduct {
  id: string | number;
  name: string;
  nameBn?: string;
  nameEn?: string;
  barcode?: string;
  price: number;
  cost?: number;
  unit: string; // e.g. "kg", "litre", "piece", "packet"
  currentStock: number;
  category?: string;
  aliases?: string[];
}

export interface POSCustomer {
  id: string | number;
  name: string;
  nameBn?: string;
  nameEn?: string;
  phone?: string;
  currentDue: number;
  aliases?: string[];
}

export interface POSDueInfo {
  customerId?: string | number;
  customerName: string;
  dueAmount: number;
  lastPaymentDate?: string;
  phone?: string;
}

export interface POSProductSalesReport {
  productId: string | number;
  productName: string;
  unit: string;
  currentStock: number;
  soldQuantity: number;
  salesTotal: number;
  days?: number;
}

export interface POSSalesSummary {
  days: number;
  salesCount: number;
  salesTotal: number;
  estimatedProfit?: number;
}

export interface POSSaleItemDraft {
  product: POSProduct;
  quantity: number;
  unitPrice: number;
  lineTotal: number;
}

export interface POSSaleDraft {
  kind: 'sale' | 'purchase' | 'due_payment';
  items: POSSaleItemDraft[];
  total: number;
  paymentType: 'cash' | 'due' | 'upi';
  customer?: POSCustomer | null;
  customerName?: string;
  hasUnknownCustomer?: boolean;
  warnings?: string[];
  rawCommand: string;
}

export interface POSSaleResult {
  success: boolean;
  saleId?: string | number;
  invoiceNumber?: string;
  error?: string;
}

/**
 * Standard Adapter Contract:
 * Provide these standard methods from your POS backend or local state.
 */
export interface ComamPOSAdapter {
  searchProducts(query: string): Promise<POSProduct[]>;
  getCustomers(): Promise<POSCustomer[]>;
  getCustomerDue(customerNameOrId: string | number): Promise<POSDueInfo | null>;
  getProductSalesReport(productNameOrId: string | number, days?: number): Promise<POSProductSalesReport | null>;
  getShopSalesSummary?(days?: number): Promise<POSSalesSummary | null>;
  createCustomer(name: string, phone?: string): Promise<POSCustomer>;
  commitSale?(draft: POSSaleDraft): Promise<POSSaleResult>;
  addToCart?(draft: POSSaleDraft): Promise<void> | void;
}
