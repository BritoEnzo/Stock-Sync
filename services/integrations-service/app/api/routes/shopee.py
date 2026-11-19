"""Rotas da integração Shopee (Shopee Open Platform v2).

Segue o mesmo desenho da integração Mercado Livre:
- ``/api/v1/auth/shopee/*``         → OAuth (connect + callback)
- ``/api/v1/integrations/shopee/*`` → credenciais, estoque e sincronização
"""

import logging
from collections import defaultdict
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import APIError, UnauthorizedError
from app.db.session import get_session
from app.models.marketplace_credential import Marketplace, MarketplaceCredential
from app.services.shopee import ITEM_LIST_MAX_PAGE_SIZE, ITEM_STATUSES, ShopeeService

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/v1/auth/shopee", tags=["shopee-auth"])
router = APIRouter(prefix="/api/v1/integrations/shopee", tags=["shopee-stock"])


def get_tenant_id(request: Request) -> str:
    """Resolve o tenant (usuário do auth-service) da requisição.

    Ordem: header X-Tenant-ID → cookie tenant_id → JWT (cookie access_token ou
    header Authorization: Bearer, que é o que o frontend envia).
    """
    tenant_id = request.headers.get("X-Tenant-ID") or request.cookies.get("tenant_id")
    if tenant_id:
        return tenant_id

    token = request.cookies.get("access_token")
    auth_header = request.headers.get("Authorization", "")
    if not token and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()

    if token:
        try:
            payload = jwt.decode(
                token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
            )
            if payload.get("tenant_id"):
                return payload["tenant_id"]
        except JWTError:
            pass

    raise UnauthorizedError("Tenant ID not found. Please authenticate first.")


def _frontend_redirect(**params: str) -> RedirectResponse:
    # O frontend usa HashRouter, então a rota fica depois de "#"
    url = f"{settings.frontend_url.rstrip('/')}/#/integrations?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


# =============================================================================
# OAuth
# =============================================================================


@auth_router.get("/connect")
def shopee_connect(request: Request, session: Session = Depends(get_session)):
    """Gera a URL de autorização da loja Shopee para o tenant autenticado."""
    tenant_id = get_tenant_id(request)
    auth_url = ShopeeService(session).get_auth_url(tenant_id)
    return {"auth_url": auth_url, "tenant_id": tenant_id}


@auth_router.get("/callback/{state}")
async def shopee_callback(
    state: str = Path(...),
    code: str | None = Query(None),
    shop_id: int | None = Query(None),
    main_account_id: int | None = Query(None),
    session: Session = Depends(get_session),
):
    """Recebe o redirect da Shopee, troca o code por tokens e salva a credencial."""
    if not code or not shop_id:
        reason = "main_account_not_supported" if main_account_id else "missing_code"
        return _frontend_redirect(shopee="error", reason=reason)

    service = ShopeeService(session)
    try:
        tenant_id = service.parse_state(state)
        token_data = await service.exchange_code_for_tokens(code, shop_id)
        try:
            shop_info = await service.get_shop_info(token_data["access_token"], shop_id)
        except APIError:
            shop_info = None  # nome da loja é opcional
        service.save_credential(tenant_id, shop_id, token_data, shop_info)
    except APIError as exc:
        logger.warning("Falha no callback Shopee: %s %s", exc.code, exc.details)
        return _frontend_redirect(shopee="error", reason=exc.code)

    return _frontend_redirect(shopee="connected", shop_id=str(shop_id))


# =============================================================================
# Credenciais
# =============================================================================


class ShopeeCredentialResponse(BaseModel):
    id: int
    marketplace: str
    account_name: str
    shop_id: int | None
    is_active: bool
    expires_at: str | None
    created_at: str
    updated_at: str


@router.get("/credentials", response_model=list[ShopeeCredentialResponse])
def list_credentials(request: Request, session: Session = Depends(get_session)):
    tenant_id = get_tenant_id(request)
    credentials = session.exec(
        select(MarketplaceCredential).where(
            MarketplaceCredential.tenant_id == tenant_id,
            MarketplaceCredential.marketplace == Marketplace.shopee,
        )
    ).all()
    return [
        ShopeeCredentialResponse(
            id=c.id,
            marketplace=c.marketplace,
            account_name=c.account_name,
            shop_id=c.shopee_shop_id,
            is_active=c.is_active,
            expires_at=c.expires_at.isoformat() if c.expires_at else None,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in credentials
    ]


@router.delete("/credentials/{credential_id}")
def delete_credential(
    credential_id: int, request: Request, session: Session = Depends(get_session)
):
    tenant_id = get_tenant_id(request)
    credential = session.get(MarketplaceCredential, credential_id)
    if not credential or credential.marketplace != Marketplace.shopee:
        raise HTTPException(status_code=404, detail="Credential not found")
    if credential.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this credential")

    session.delete(credential)
    session.commit()
    return {"message": "Shopee connection removed successfully"}


# =============================================================================
# Estoque
# =============================================================================


class LocalSkuInfo(BaseModel):
    sku_id: int
    product_id: int
    internal_sku: str
    stock_quantity: int
    price: float | None
    matched_by: str


class ShopeeStockRow(BaseModel):
    item_id: int
    model_id: int | None
    item_name: str | None
    variation_name: str | None
    item_status: str | None
    shopee_sku: str | None
    price: float | None
    original_price: float | None
    currency: str | None
    available_stock: int
    reserved_stock: int
    image_url: str | None
    permalink: str
    local: LocalSkuInfo | None = None
    stock_difference: int | None = None


class ShopeeStockResponse(BaseModel):
    shop_id: int
    items: list[ShopeeStockRow]
    offset: int
    limit: int
    total: int
    has_next_page: bool
    next_offset: int | None
    catalog_available: bool


def _parse_statuses(status: list[str] | None) -> tuple[str, ...]:
    if not status:
        return ("NORMAL", "UNLIST")
    values = tuple(s.upper() for s in status)
    invalid = [s for s in values if s not in ITEM_STATUSES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Status inválido: {', '.join(invalid)}. Use: {', '.join(ITEM_STATUSES)}",
        )
    return values


async def _build_rows(
    service: ShopeeService, credential: MarketplaceCredential, item_ids: list[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    items = await service.fetch_items_with_models(credential, item_ids)
    rows = service.to_stock_rows(credential, items)
    catalog = await service.fetch_catalog()
    if catalog is not None:
        service.match_catalog(rows, *catalog)
    return items, rows, catalog is not None


@router.get("/stock", response_model=ShopeeStockResponse)
async def get_stock(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    status: list[str] | None = Query(None, description="NORMAL, UNLIST, BANNED, REVIEWING"),
    session: Session = Depends(get_session),
):
    """Estoque dos anúncios da loja, uma linha por SKU (item ou variação).

    Cada linha traz o estoque disponível/reservado na Shopee e, quando encontrado,
    o SKU interno correspondente do catalog-service com a diferença de estoque.
    """
    tenant_id = get_tenant_id(request)
    service = ShopeeService(session)
    credential = await service.require_credential(tenant_id)

    page = await service.list_item_ids(credential, offset, limit, _parse_statuses(status))
    _, rows, catalog_ok = await _build_rows(service, credential, page["item_ids"])

    return ShopeeStockResponse(
        shop_id=credential.shopee_shop_id,
        items=rows,
        offset=offset,
        limit=limit,
        total=page["total"],
        has_next_page=page["has_next_page"],
        next_offset=page["next_offset"],
        catalog_available=catalog_ok,
    )


@router.get("/stock/{item_id}", response_model=list[ShopeeStockRow])
async def get_item_stock(item_id: int, request: Request, session: Session = Depends(get_session)):
    """Estoque de um anúncio específico (todas as variações)."""
    tenant_id = get_tenant_id(request)
    service = ShopeeService(session)
    credential = await service.require_credential(tenant_id)

    _, rows, _ = await _build_rows(service, credential, [item_id])
    if not rows:
        raise HTTPException(status_code=404, detail="Item not found on Shopee")
    return rows


class ShopeeSyncResponse(BaseModel):
    shop_id: int
    items_synced: int
    skus_found: int
    skus_matched: int
    skus_with_divergence: int
    catalog_available: bool


@router.post("/sync", response_model=ShopeeSyncResponse)
async def sync_stock(
    request: Request,
    status: list[str] | None = Query(None),
    session: Session = Depends(get_session),
):
    """Percorre todos os anúncios da loja e grava/atualiza em ``marketplace_items``."""
    tenant_id = get_tenant_id(request)
    service = ShopeeService(session)
    credential = await service.require_credential(tenant_id)
    statuses = _parse_statuses(status)

    item_ids: list[int] = []
    offset = 0
    while True:
        page = await service.list_item_ids(credential, offset, ITEM_LIST_MAX_PAGE_SIZE, statuses)
        item_ids.extend(page["item_ids"])
        if not page["has_next_page"] or page["next_offset"] is None:
            break
        offset = page["next_offset"]

    items, rows, catalog_ok = await _build_rows(service, credential, item_ids)

    rows_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_item[row["item_id"]].append(row)
    for item in items:
        service.sync_item_to_local(credential, item, rows_by_item[item["item_id"]])

    return ShopeeSyncResponse(
        shop_id=credential.shopee_shop_id,
        items_synced=len(items),
        skus_found=len(rows),
        skus_matched=sum(1 for r in rows if r.get("local")),
        skus_with_divergence=sum(1 for r in rows if r.get("stock_difference")),
        catalog_available=catalog_ok,
    )
