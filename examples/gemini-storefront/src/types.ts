export interface Product {
  product_id: string;
  title: string;
  price: number;
  currency: string;
  in_stock: boolean;
  image_url?: string;
  category?: string;
  description?: string;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
}

export interface CheckoutLineItem {
  product_id: string;
  title: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  currency: string;
}

export interface CheckoutSession {
  checkout_session_id: string;
  status: string;
  line_items: CheckoutLineItem[];
  subtotal: number;
  currency: string;
  payment_handler: string;
  buyer?: { name?: string; email?: string };
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  products?: Product[];
  toolCalls?: ToolCall[];
  checkoutSession?: CheckoutSession;
  loading?: boolean;
}

export interface CartItem {
  product: Product;
  quantity: number;
}

export interface QuoteItem {
  product_id: string;
  title: string;
  price: number;
  currency: string;
  image_url?: string;
  quantity: number;
}

export interface Quote {
  quote_id: string;
  name: string;
  items: QuoteItem[];
  total: number;
  currency: string;
  created_at: string;
}
