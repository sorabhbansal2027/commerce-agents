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

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  products?: Product[];
  toolCalls?: ToolCall[];
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
