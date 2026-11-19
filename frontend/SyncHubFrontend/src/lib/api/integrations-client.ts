import { httpClient } from "./http-client";

export interface MarketplaceCredential {
  id: number;
  tenant_id: string;
  marketplace: "mercado_livre" | "shopee";
  account_name: string;
  ml_user_id: number | null;
  access_token: string;
  refresh_token: string | null;
  expires_at: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateItemRequest {
  title: string;
  category_id: string;
  price: number;
  currency_id?: string;
  available_quantity: number;
  buying_mode?: string;
  listing_type_id?: string;
  condition?: string;
  description?: string;
  pictures?: Array<{ source: string }>;
  attributes?: Array<{ id: string; value_name: string }>;
}

export interface CreateItemResponse {
  id: string;
  title: string;
  permalink: string;
  status: string;
}

export interface ConnectResponse {
  auth_url: string;
  tenant_id: string;
}

export interface CallbackResponse {
  message: string;
  credential_id: number;
  ml_user_id: number;
  account_name: string;
}

export interface CredentialResponse {
  id: number;
  marketplace: "mercado_livre" | "shopee";
  account_name: string;
  ml_user_id: number | null;
  ml_nickname: string | null;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShopeeCredentialResponse {
  id: number;
  marketplace: "shopee";
  account_name: string;
  shop_id: number | null;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShopeeLocalSku {
  sku_id: number;
  product_id: number;
  internal_sku: string;
  stock_quantity: number;
  price: number | null;
  matched_by: "platform_mapping" | "internal_sku";
}

export interface ShopeeStockRow {
  item_id: number;
  model_id: number | null;
  item_name: string | null;
  variation_name: string | null;
  item_status: string | null;
  shopee_sku: string | null;
  price: number | null;
  original_price: number | null;
  currency: string | null;
  available_stock: number;
  reserved_stock: number;
  image_url: string | null;
  permalink: string;
  local: ShopeeLocalSku | null;
  /** available_stock (Shopee) - stock_quantity (Stock Sync). */
  stock_difference: number | null;
}

export interface ShopeeStockResponse {
  shop_id: number;
  items: ShopeeStockRow[];
  offset: number;
  limit: number;
  total: number;
  has_next_page: boolean;
  next_offset: number | null;
  catalog_available: boolean;
}

export interface ShopeeSyncResponse {
  shop_id: number;
  items_synced: number;
  skus_found: number;
  skus_matched: number;
  skus_with_divergence: number;
  catalog_available: boolean;
}

export const integrationsClient = {
  // Mercado Livre OAuth
  async getMercadoLivreAuthUrl(): Promise<ConnectResponse> {
    return httpClient.get<ConnectResponse>("/api/v1/auth/mercadolivre/connect");
  },

  // Credentials
  async listMercadoLivreCredentials(): Promise<CredentialResponse[]> {
    return httpClient.get<CredentialResponse[]>("/api/v1/integrations/mercadolivre/credentials");
  },

  async deleteMercadoLivreCredential(credentialId: number): Promise<{ message: string }> {
    return httpClient.delete<{ message: string }>(`/api/v1/integrations/mercadolivre/credentials/${credentialId}`);
  },

  // Produtos
  async createMercadoLivreItem(payload: CreateItemRequest): Promise<CreateItemResponse> {
    return httpClient.post<CreateItemResponse>("/api/v1/integrations/mercadolivre/items", payload);
  },

  // Webhooks (called by Mercado Livre, not typically from frontend)

  // Shopee (Open Platform v2)
  async getShopeeAuthUrl(): Promise<ConnectResponse> {
    return httpClient.get<ConnectResponse>("/api/v1/auth/shopee/connect");
  },

  async listShopeeCredentials(): Promise<ShopeeCredentialResponse[]> {
    return httpClient.get<ShopeeCredentialResponse[]>("/api/v1/integrations/shopee/credentials");
  },

  async deleteShopeeCredential(credentialId: number): Promise<{ message: string }> {
    return httpClient.delete<{ message: string }>(`/api/v1/integrations/shopee/credentials/${credentialId}`);
  },

  async getShopeeStock(params: { offset?: number; limit?: number } = {}): Promise<ShopeeStockResponse> {
    return httpClient.get<ShopeeStockResponse>("/api/v1/integrations/shopee/stock", params);
  },

  async syncShopeeStock(): Promise<ShopeeSyncResponse> {
    return httpClient.post<ShopeeSyncResponse>("/api/v1/integrations/shopee/sync", {});
  },
};