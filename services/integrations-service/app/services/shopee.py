"""Integração com a Shopee Open Platform (API v2).

Fluxo:
1. ``get_auth_url`` gera o link de autorização da loja (``/api/v2/shop/auth_partner``).
2. A Shopee redireciona para ``shopee_redirect_url/{state}?code=...&shop_id=...``.
3. ``exchange_code_for_tokens`` troca o ``code`` por access/refresh token.
4. As chamadas de loja (produtos, estoque) são assinadas com HMAC-SHA256 usando a
   partner key, e o token é renovado automaticamente quando está perto de expirar.

Assinatura (sign) = HMAC-SHA256(partner_key, base) em hexadecimal, onde ``base`` é:
- APIs públicas/auth: ``partner_id + path + timestamp``
- APIs de loja:       ``partner_id + path + timestamp + access_token + shop_id``
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import APIError, ConflictError, UnauthorizedError
from app.models.marketplace_credential import Marketplace, MarketplaceCredential, MarketplaceItem

AUTH_PARTNER_PATH = "/api/v2/shop/auth_partner"
TOKEN_GET_PATH = "/api/v2/auth/token/get"
TOKEN_REFRESH_PATH = "/api/v2/auth/access_token/get"
SHOP_INFO_PATH = "/api/v2/shop/get_shop_info"
ITEM_LIST_PATH = "/api/v2/product/get_item_list"
ITEM_BASE_INFO_PATH = "/api/v2/product/get_item_base_info"
MODEL_LIST_PATH = "/api/v2/product/get_model_list"

# Status de anúncio aceitos por get_item_list
ITEM_STATUSES = ("NORMAL", "UNLIST", "BANNED", "REVIEWING")
DEFAULT_ITEM_STATUSES = ("NORMAL", "UNLIST")

# Limites da API
ITEM_LIST_MAX_PAGE_SIZE = 100
BASE_INFO_MAX_IDS = 50

# Validade do "state" do OAuth
STATE_TTL_SECONDS = 15 * 60

# Erros da Shopee que indicam token inválido/expirado
AUTH_ERRORS = {"error_auth", "invalid_access_token", "invalid_acceess_token", "error_permission"}


class ShopeeService:
    def __init__(self, session: Session):
        self.session = session
        self.partner_id = settings.shopee_partner_id.strip()
        self.partner_key = settings.shopee_partner_key.strip()
        self.host = settings.shopee_api_host.rstrip("/")
        self.auth_host = (settings.shopee_auth_host or settings.shopee_api_host).rstrip("/")
        self.redirect_url = settings.shopee_redirect_url.rstrip("/")

    # =========================================================================
    # Assinatura e chamadas HTTP
    # =========================================================================

    def _ensure_configured(self) -> None:
        if not self.partner_id or not self.partner_key:
            raise APIError(
                status_code=503,
                code="shopee_not_configured",
                message=(
                    "Integração Shopee não configurada. Defina SHOPEE_PARTNER_ID e "
                    "SHOPEE_PARTNER_KEY com os dados do app no Shopee Open Platform Console."
                ),
            )

    def _sign(
        self,
        path: str,
        timestamp: int,
        access_token: str | None = None,
        shop_id: int | None = None,
    ) -> str:
        base = f"{self.partner_id}{path}{timestamp}"
        if access_token is not None and shop_id is not None:
            base += f"{access_token}{shop_id}"
        return hmac.new(self.partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

    def _public_params(self, path: str) -> dict[str, Any]:
        timestamp = int(time.time())
        return {
            "partner_id": int(self.partner_id),
            "timestamp": timestamp,
            "sign": self._sign(path, timestamp),
        }

    def _shop_params(self, path: str, access_token: str, shop_id: int) -> dict[str, Any]:
        timestamp = int(time.time())
        return {
            "partner_id": int(self.partner_id),
            "timestamp": timestamp,
            "access_token": access_token,
            "shop_id": shop_id,
            "sign": self._sign(path, timestamp, access_token, shop_id),
        }

    @staticmethod
    def _raise_for_shopee_error(response: httpx.Response, path: str) -> dict[str, Any]:
        """Valida a resposta. A Shopee pode retornar erro com HTTP 200 e ``error`` preenchido."""
        try:
            data = response.json()
        except ValueError:
            data = {"error": "invalid_response", "message": response.text[:500]}

        error = data.get("error")
        if response.status_code >= 400 or error:
            details = {
                "path": path,
                "status_code": response.status_code,
                "error": error,
                "message": data.get("message"),
                "request_id": data.get("request_id"),
            }
            if error in AUTH_ERRORS or response.status_code in (401, 403):
                raise APIError(
                    status_code=401,
                    code="shopee_unauthorized",
                    message="A Shopee recusou a autenticação. Reconecte a loja.",
                    details=details,
                )
            raise APIError(
                status_code=502,
                code="shopee_api_error",
                message=f"Erro na API da Shopee: {data.get('message') or error}",
                details=details,
            )
        return data

    async def _shop_get(
        self, credential: MarketplaceCredential, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        query = {
            **self._shop_params(path, credential.access_token, credential.shopee_shop_id),
            **params,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.host}{path}", params=query)
        return self._raise_for_shopee_error(response, path)

    # =========================================================================
    # OAuth
    # =========================================================================

    def _state_signature(self, tenant_id: str, issued_at: int) -> str:
        msg = f"{tenant_id}.{issued_at}".encode()
        return hmac.new(settings.jwt_secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]

    def build_state(self, tenant_id: str) -> str:
        issued_at = int(time.time())
        return f"{tenant_id}.{issued_at}.{self._state_signature(tenant_id, issued_at)}"

    def parse_state(self, state: str) -> str:
        """Valida o state assinado e devolve o tenant_id."""
        try:
            tenant_id, issued_at_raw, signature = state.rsplit(".", 2)
            issued_at = int(issued_at_raw)
        except ValueError as exc:
            raise UnauthorizedError("State de autorização inválido.") from exc

        expected = self._state_signature(tenant_id, issued_at)
        if not hmac.compare_digest(signature, expected):
            raise UnauthorizedError("State de autorização inválido.")
        if time.time() - issued_at > STATE_TTL_SECONDS:
            raise UnauthorizedError("Link de autorização expirado. Tente conectar novamente.")
        return tenant_id

    def get_auth_url(self, tenant_id: str) -> str:
        """Gera a URL de autorização da loja na Shopee.

        O state vai no path do redirect para não depender de como a Shopee
        concatena ``code`` e ``shop_id`` na query string.
        """
        self._ensure_configured()
        params = self._public_params(AUTH_PARTNER_PATH)
        params["redirect"] = f"{self.redirect_url}/{self.build_state(tenant_id)}"
        return f"{self.auth_host}{AUTH_PARTNER_PATH}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str, shop_id: int) -> dict[str, Any]:
        self._ensure_configured()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.host}{TOKEN_GET_PATH}",
                params=self._public_params(TOKEN_GET_PATH),
                json={"code": code, "shop_id": shop_id, "partner_id": int(self.partner_id)},
            )
        return self._raise_for_shopee_error(response, TOKEN_GET_PATH)

    async def refresh_access_token(self, refresh_token: str, shop_id: int) -> dict[str, Any]:
        self._ensure_configured()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.host}{TOKEN_REFRESH_PATH}",
                params=self._public_params(TOKEN_REFRESH_PATH),
                json={
                    "refresh_token": refresh_token,
                    "shop_id": shop_id,
                    "partner_id": int(self.partner_id),
                },
            )
        return self._raise_for_shopee_error(response, TOKEN_REFRESH_PATH)

    async def get_shop_info(self, access_token: str, shop_id: int) -> dict[str, Any]:
        query = self._shop_params(SHOP_INFO_PATH, access_token, shop_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.host}{SHOP_INFO_PATH}", params=query)
        data = self._raise_for_shopee_error(response, SHOP_INFO_PATH)
        # Os campos vêm no nível raiz; algumas versões aninham em "response"
        return data.get("response") or data

    # =========================================================================
    # Credenciais
    # =========================================================================

    def save_credential(
        self,
        tenant_id: str,
        shop_id: int,
        token_data: dict[str, Any],
        shop_info: dict[str, Any] | None = None,
    ) -> MarketplaceCredential:
        """Cria ou atualiza a credencial Shopee do tenant (uma loja por tenant, como no ML)."""
        expires_at = datetime.now(UTC) + timedelta(seconds=token_data.get("expire_in", 14400))
        shop_name = (shop_info or {}).get("shop_name")

        owner = self.session.exec(
            select(MarketplaceCredential).where(MarketplaceCredential.shopee_shop_id == shop_id)
        ).first()
        if owner and owner.tenant_id != tenant_id:
            raise ConflictError("Esta loja Shopee já está conectada a outra conta do Stock Sync.")

        credential = (
            owner
            or self.session.exec(
                select(MarketplaceCredential).where(
                    MarketplaceCredential.tenant_id == tenant_id,
                    MarketplaceCredential.marketplace == Marketplace.shopee,
                )
            ).first()
        )

        if credential is None:
            credential = MarketplaceCredential(
                tenant_id=tenant_id,
                marketplace=Marketplace.shopee,
                account_name=shop_name or f"Shopee {shop_id}",
                shopee_shop_id=shop_id,
                access_token=token_data["access_token"],
            )

        credential.shopee_shop_id = shop_id
        credential.access_token = token_data["access_token"]
        credential.refresh_token = token_data.get("refresh_token")
        credential.expires_at = expires_at
        credential.account_name = shop_name or credential.account_name
        credential.is_active = True
        credential.updated_at = datetime.now(UTC)

        self.session.add(credential)
        self.session.commit()
        self.session.refresh(credential)
        return credential

    async def get_valid_credential(self, tenant_id: str) -> MarketplaceCredential | None:
        """Retorna a credencial ativa, renovando o token se faltar menos de 5 minutos."""
        credential = self.session.exec(
            select(MarketplaceCredential).where(
                MarketplaceCredential.tenant_id == tenant_id,
                MarketplaceCredential.marketplace == Marketplace.shopee,
                MarketplaceCredential.is_active == True,  # noqa: E712
            )
        ).first()
        if not credential or not credential.shopee_shop_id:
            return None

        if credential.expires_at:
            expires_at = credential.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC) + timedelta(minutes=5):
                return await self._refresh_credential(credential)

        return credential

    async def _refresh_credential(
        self, credential: MarketplaceCredential
    ) -> MarketplaceCredential | None:
        if not credential.refresh_token:
            return None
        try:
            token_data = await self.refresh_access_token(
                credential.refresh_token, credential.shopee_shop_id
            )
        except APIError:
            return None

        credential.access_token = token_data["access_token"]
        # A Shopee rotaciona o refresh token a cada renovação
        credential.refresh_token = token_data.get("refresh_token", credential.refresh_token)
        credential.expires_at = datetime.now(UTC) + timedelta(
            seconds=token_data.get("expire_in", 14400)
        )
        credential.updated_at = datetime.now(UTC)
        self.session.add(credential)
        self.session.commit()
        self.session.refresh(credential)
        return credential

    async def require_credential(self, tenant_id: str) -> MarketplaceCredential:
        self._ensure_configured()
        credential = await self.get_valid_credential(tenant_id)
        if not credential:
            raise APIError(
                status_code=401,
                code="unauthorized",
                message="No valid Shopee credential found",
            )
        return credential

    # =========================================================================
    # Produtos e estoque
    # =========================================================================

    async def list_item_ids(
        self,
        credential: MarketplaceCredential,
        offset: int,
        page_size: int,
        statuses: tuple[str, ...] = DEFAULT_ITEM_STATUSES,
    ) -> dict[str, Any]:
        """``get_item_list``: devolve ids dos anúncios, total e paginação."""
        data = await self._shop_get(
            credential,
            ITEM_LIST_PATH,
            {
                "offset": offset,
                "page_size": min(page_size, ITEM_LIST_MAX_PAGE_SIZE),
                # httpx repete a chave: item_status=NORMAL&item_status=UNLIST
                "item_status": list(statuses),
            },
        )
        response = data.get("response") or {}
        return {
            "item_ids": [i["item_id"] for i in response.get("item") or []],
            "total": response.get("total_count", 0),
            "has_next_page": bool(response.get("has_next_page")),
            "next_offset": response.get("next_offset"),
        }

    async def get_items_base_info(
        self, credential: MarketplaceCredential, item_ids: list[int]
    ) -> list[dict[str, Any]]:
        """``get_item_base_info`` em lotes de 50 ids."""
        items: list[dict[str, Any]] = []
        for start in range(0, len(item_ids), BASE_INFO_MAX_IDS):
            chunk = item_ids[start : start + BASE_INFO_MAX_IDS]
            data = await self._shop_get(
                credential,
                ITEM_BASE_INFO_PATH,
                {"item_id_list": ",".join(str(i) for i in chunk)},
            )
            items.extend((data.get("response") or {}).get("item_list") or [])
        return items

    async def get_model_list(
        self, credential: MarketplaceCredential, item_id: int
    ) -> dict[str, Any]:
        """``get_model_list``: variações (modelos) com preço e estoque próprios."""
        data = await self._shop_get(credential, MODEL_LIST_PATH, {"item_id": item_id})
        return data.get("response") or {}

    async def fetch_items_with_models(
        self, credential: MarketplaceCredential, item_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Base info de cada item + lista de modelos quando o item tem variações."""
        items = await self.get_items_base_info(credential, item_ids)
        for item in items:
            item["_models"] = None
            if item.get("has_model"):
                item["_models"] = await self.get_model_list(credential, item["item_id"])
        return items

    # =========================================================================
    # Normalização para o formato de estoque do Stock Sync
    # =========================================================================

    @staticmethod
    def _stock_from(entity: dict[str, Any]) -> tuple[int, int]:
        """Extrai (disponível, reservado) de ``stock_info_v2``, com fallback ao formato antigo."""
        stock_v2 = entity.get("stock_info_v2") or {}
        summary = stock_v2.get("summary_info") or {}
        if summary:
            return (
                int(summary.get("total_available_stock") or 0),
                int(summary.get("total_reserved_stock") or 0),
            )
        seller_stock = stock_v2.get("seller_stock") or []
        if seller_stock:
            return sum(int(s.get("stock") or 0) for s in seller_stock), 0

        legacy = entity.get("stock_info") or []
        if legacy:
            return (
                sum(int(s.get("current_stock") or 0) for s in legacy),
                sum(int(s.get("reserved_stock") or 0) for s in legacy),
            )
        return 0, 0

    @staticmethod
    def _price_from(entity: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
        price_info = entity.get("price_info") or []
        if not price_info:
            return None, None, None
        first = price_info[0]
        return first.get("current_price"), first.get("original_price"), first.get("currency")

    @staticmethod
    def _variation_name(model: dict[str, Any], tier_variation: list[dict[str, Any]]) -> str | None:
        if model.get("model_name"):
            return model["model_name"]
        parts = []
        for tier_pos, option_idx in enumerate(model.get("tier_index") or []):
            try:
                parts.append(tier_variation[tier_pos]["option_list"][option_idx]["option"])
            except (IndexError, KeyError, TypeError):
                continue
        return " / ".join(parts) or None

    def _permalink(self, shop_id: int, item_id: int) -> str:
        return f"{settings.shopee_shop_url.rstrip('/')}/product/{shop_id}/{item_id}"

    def to_stock_rows(
        self, credential: MarketplaceCredential, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Achata itens/modelos em linhas de estoque (uma por SKU vendável)."""
        rows: list[dict[str, Any]] = []
        for item in items:
            images = (item.get("image") or {}).get("image_url_list") or []
            common = {
                "item_id": item["item_id"],
                "item_name": item.get("item_name"),
                "item_status": item.get("item_status"),
                "image_url": images[0] if images else None,
                "permalink": self._permalink(credential.shopee_shop_id, item["item_id"]),
            }
            models_payload = item.get("_models") or {}
            models = models_payload.get("model") or []

            if not models:
                available, reserved = self._stock_from(item)
                price, original_price, currency = self._price_from(item)
                rows.append(
                    {
                        **common,
                        "model_id": None,
                        "variation_name": None,
                        "shopee_sku": item.get("item_sku") or None,
                        "price": price,
                        "original_price": original_price,
                        "currency": currency,
                        "available_stock": available,
                        "reserved_stock": reserved,
                    }
                )
                continue

            tiers = models_payload.get("tier_variation") or []
            for model in models:
                available, reserved = self._stock_from(model)
                price, original_price, currency = self._price_from(model)
                rows.append(
                    {
                        **common,
                        "model_id": model.get("model_id"),
                        "variation_name": self._variation_name(model, tiers),
                        "shopee_sku": model.get("model_sku") or item.get("item_sku") or None,
                        "price": price,
                        "original_price": original_price,
                        "currency": currency,
                        "available_stock": available,
                        "reserved_stock": reserved,
                    }
                )
        return rows

    # =========================================================================
    # Cruzamento com o catálogo interno (catalog-service)
    # =========================================================================

    @staticmethod
    async def fetch_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Busca SKUs e mapeamentos do catalog-service. ``None`` se indisponível."""
        base = settings.catalog_service_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                skus_resp = await client.get(f"{base}/api/v1/skus")
                maps_resp = await client.get(f"{base}/api/v1/platform-mappings")
                skus_resp.raise_for_status()
                maps_resp.raise_for_status()
                return skus_resp.json(), maps_resp.json()
        except (httpx.HTTPError, ValueError):
            return None

    @staticmethod
    def match_catalog(
        rows: list[dict[str, Any]],
        skus: list[dict[str, Any]],
        mappings: list[dict[str, Any]],
    ) -> None:
        """Anexa o SKU interno a cada linha.

        Prioridade: PlatformMapping (shopee) por item/modelo → PlatformMapping por
        platform_sku → SKU interno com o mesmo código do SKU da Shopee.
        """
        skus_by_id = {s["id"]: s for s in skus}
        skus_by_code = {s["internal_sku"]: s for s in skus}
        shopee_maps = [
            m for m in mappings if m.get("platform") == "shopee" and m.get("is_active", True)
        ]
        by_ids = {
            (m.get("platform_product_id"), m.get("platform_variant_id")): m for m in shopee_maps
        }
        by_platform_sku = {m.get("platform_sku"): m for m in shopee_maps}

        for row in rows:
            item_key = str(row["item_id"])
            model_key = str(row["model_id"]) if row["model_id"] is not None else None

            mapping = by_ids.get((item_key, model_key)) or (
                by_ids.get((item_key, None)) if model_key is None else None
            )
            if mapping is None and row["shopee_sku"]:
                mapping = by_platform_sku.get(row["shopee_sku"])

            sku = skus_by_id.get(mapping["sku_id"]) if mapping else None
            matched_by = "platform_mapping" if sku else None
            if sku is None and row["shopee_sku"]:
                sku = skus_by_code.get(row["shopee_sku"])
                matched_by = "internal_sku" if sku else None

            if sku is None:
                row["local"] = None
                row["stock_difference"] = None
                continue

            row["local"] = {
                "sku_id": sku["id"],
                "product_id": sku["product_id"],
                "internal_sku": sku["internal_sku"],
                "stock_quantity": sku["stock_quantity"],
                "price": sku.get("price"),
                "matched_by": matched_by,
            }
            # Positivo: Shopee tem mais estoque que o sistema (risco de overselling)
            row["stock_difference"] = row["available_stock"] - sku["stock_quantity"]

    # =========================================================================
    # Persistência local (marketplace_items)
    # =========================================================================

    def sync_item_to_local(
        self,
        credential: MarketplaceCredential,
        item: dict[str, Any],
        item_rows: list[dict[str, Any]],
    ) -> MarketplaceItem:
        """Upsert do anúncio na tabela ``marketplace_items`` (estoque somado das variações)."""
        item_id = str(item["item_id"])
        prices = [r["price"] for r in item_rows if r["price"] is not None]
        local_skus = {r["local"]["sku_id"] for r in item_rows if r.get("local")}
        local_products = {r["local"]["product_id"] for r in item_rows if r.get("local")}
        values = {
            "title": (item.get("item_name") or f"Shopee {item_id}")[:255],
            "price": min(prices) if prices else 0.0,
            "available_quantity": sum(r["available_stock"] for r in item_rows),
            "status": item.get("item_status"),
            "thumbnail": item_rows[0]["image_url"] if item_rows else None,
            "permalink": self._permalink(credential.shopee_shop_id, item["item_id"]),
            "category_id": str(item["category_id"]) if item.get("category_id") else None,
            "condition": (item.get("condition") or "").lower()[:10] or None,
            # Só vincula quando o anúncio aponta para um único SKU/produto interno
            "sku_id": next(iter(local_skus)) if len(local_skus) == 1 else None,
            "product_id": next(iter(local_products)) if len(local_products) == 1 else None,
            "raw_payload": item,
            "last_synced_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        existing = self.session.exec(
            select(MarketplaceItem).where(
                MarketplaceItem.tenant_id == credential.tenant_id,
                MarketplaceItem.marketplace == Marketplace.shopee.value,
                MarketplaceItem.item_id == item_id,
            )
        ).first()

        record = existing or MarketplaceItem(
            item_id=item_id,
            tenant_id=credential.tenant_id,
            marketplace=Marketplace.shopee.value,
            **{k: v for k, v in values.items() if k != "updated_at"},
        )
        if existing:
            for key, value in values.items():
                if key in ("sku_id", "product_id") and value is None:
                    continue  # não apaga vínculos feitos anteriormente
                setattr(existing, key, value)

        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record
