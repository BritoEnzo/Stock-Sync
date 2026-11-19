"""Testes da integração Shopee (sem rede: a API é simulada com httpx.MockTransport)."""

import hashlib
import hmac
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.core.config import settings
from app.core.errors import APIError, UnauthorizedError
from app.services import shopee as shopee_module
from app.services.shopee import ShopeeService

PARTNER_ID = "123456"
PARTNER_KEY = "test-partner-key"
SHOP_ID = 987654
ACCESS_TOKEN = "token-abc"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(settings, "shopee_partner_id", PARTNER_ID)
    monkeypatch.setattr(settings, "shopee_partner_key", PARTNER_KEY)
    monkeypatch.setattr(settings, "shopee_api_host", "https://shopee.test")
    monkeypatch.setattr(settings, "shopee_redirect_url", "http://localhost:5173/cb")
    return ShopeeService(session=None)


@pytest.fixture
def credential():
    return SimpleNamespace(tenant_id="tenant-1", shopee_shop_id=SHOP_ID, access_token=ACCESS_TOKEN)


def expected_sign(base: str) -> str:
    return hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- assinatura


def test_public_sign_uses_partner_path_timestamp(service):
    assert service._sign("/api/v2/auth/token/get", 1700000000) == expected_sign(
        f"{PARTNER_ID}/api/v2/auth/token/get1700000000"
    )


def test_shop_sign_appends_access_token_and_shop_id(service):
    sign = service._sign("/api/v2/product/get_item_list", 1700000000, ACCESS_TOKEN, SHOP_ID)
    assert sign == expected_sign(
        f"{PARTNER_ID}/api/v2/product/get_item_list1700000000{ACCESS_TOKEN}{SHOP_ID}"
    )


def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "shopee_partner_id", "")
    with pytest.raises(APIError) as exc:
        ShopeeService(session=None).get_auth_url("tenant-1")
    assert exc.value.code == "shopee_not_configured"


# --------------------------------------------------------------------------- OAuth state


def test_auth_url_contains_signed_redirect_with_state(service):
    url = urlparse(service.get_auth_url("tenant-1"))
    query = parse_qs(url.query)
    assert url.path == "/api/v2/shop/auth_partner"
    assert query["partner_id"] == [PARTNER_ID]
    ts = query["timestamp"][0]
    assert query["sign"] == [expected_sign(f"{PARTNER_ID}/api/v2/shop/auth_partner{ts}")]

    redirect = query["redirect"][0]
    assert redirect.startswith("http://localhost:5173/cb/")
    assert service.parse_state(redirect.rsplit("/", 1)[1]) == "tenant-1"


def test_tampered_state_is_rejected(service):
    state = service.build_state("tenant-1")
    tenant, issued, sig = state.rsplit(".", 2)
    with pytest.raises(UnauthorizedError):
        service.parse_state(f"tenant-2.{issued}.{sig}")


def test_expired_state_is_rejected(service, monkeypatch):
    state = service.build_state("tenant-1")
    monkeypatch.setattr(time, "time", lambda: int(state.split(".")[1]) + 16 * 60)
    with pytest.raises(UnauthorizedError):
        service.parse_state(state)


# --------------------------------------------------------------------------- normalização

ITEM_SIMPLE = {
    "item_id": 1001,
    "item_name": "Camiseta Básica",
    "item_sku": "CAM-001",
    "item_status": "NORMAL",
    "has_model": False,
    "price_info": [{"current_price": 49.9, "original_price": 59.9, "currency": "BRL"}],
    "stock_info_v2": {"summary_info": {"total_available_stock": 12, "total_reserved_stock": 2}},
    "image": {"image_url_list": ["https://img/1.jpg"]},
    "category_id": 100017,
    "condition": "NEW",
}

ITEM_WITH_MODELS = {
    "item_id": 2002,
    "item_name": "Tênis Corrida",
    "item_sku": "",
    "item_status": "NORMAL",
    "has_model": True,
    "image": {"image_url_list": []},
}

MODELS_2002 = {
    "tier_variation": [
        {"name": "Tamanho", "option_list": [{"option": "40"}, {"option": "41"}]},
    ],
    "model": [
        {
            "model_id": 1,
            "model_sku": "TEN-40",
            "tier_index": [0],
            "price_info": [{"current_price": 199.0, "original_price": 199.0, "currency": "BRL"}],
            "stock_info_v2": {"seller_stock": [{"location_id": "BR", "stock": 5}]},
        },
        {
            "model_id": 2,
            "model_sku": "TEN-41",
            "tier_index": [1],
            "price_info": [{"current_price": 189.0, "original_price": 199.0, "currency": "BRL"}],
            "stock_info_v2": {"summary_info": {"total_available_stock": 0}},
        },
    ],
}


def test_to_stock_rows_flattens_items_and_models(service, credential):
    rows = service.to_stock_rows(
        credential, [ITEM_SIMPLE, {**ITEM_WITH_MODELS, "_models": MODELS_2002}]
    )
    assert len(rows) == 3

    simple = rows[0]
    assert simple["model_id"] is None
    assert simple["shopee_sku"] == "CAM-001"
    assert (simple["available_stock"], simple["reserved_stock"]) == (12, 2)
    assert simple["price"] == 49.9
    assert simple["permalink"] == f"https://shopee.com.br/product/{SHOP_ID}/1001"

    size40, size41 = rows[1], rows[2]
    assert size40["variation_name"] == "40"
    assert size40["available_stock"] == 5  # soma de seller_stock
    assert size41["shopee_sku"] == "TEN-41"
    assert size41["available_stock"] == 0


def test_legacy_stock_info_fallback(service):
    assert service._stock_from({"stock_info": [{"current_stock": 7, "reserved_stock": 1}]}) == (
        7,
        1,
    )
    assert service._stock_from({}) == (0, 0)


# --------------------------------------------------------------------------- catálogo


def test_match_catalog_prefers_mapping_then_internal_sku(service, credential):
    rows = service.to_stock_rows(
        credential, [ITEM_SIMPLE, {**ITEM_WITH_MODELS, "_models": MODELS_2002}]
    )
    skus = [
        {"id": 1, "product_id": 10, "internal_sku": "CAM-001", "stock_quantity": 10, "price": 50},
        {"id": 2, "product_id": 20, "internal_sku": "INT-TEN-40", "stock_quantity": 5, "price": 1},
    ]
    mappings = [
        # variação 40 mapeada explicitamente (SKU diferente do interno)
        {
            "sku_id": 2,
            "platform": "shopee",
            "platform_sku": "TEN-40",
            "platform_product_id": "2002",
            "platform_variant_id": "1",
            "is_active": True,
        },
        # mapeamento do Mercado Livre não deve interferir
        {
            "sku_id": 1,
            "platform": "mercado_livre",
            "platform_sku": "TEN-41",
            "platform_product_id": "2002",
            "platform_variant_id": "2",
            "is_active": True,
        },
    ]
    service.match_catalog(rows, skus, mappings)

    camiseta, size40, size41 = rows
    assert camiseta["local"]["matched_by"] == "internal_sku"
    assert camiseta["stock_difference"] == 2  # 12 na Shopee vs 10 no sistema
    assert size40["local"]["sku_id"] == 2
    assert size40["local"]["matched_by"] == "platform_mapping"
    assert size40["stock_difference"] == 0
    assert size41["local"] is None
    assert size41["stock_difference"] is None


# --------------------------------------------------------------------------- fluxo HTTP


def install_fake_shopee(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(shopee_module.httpx, "AsyncClient", factory)


@pytest.mark.anyio
async def test_full_stock_flow_against_fake_api(service, credential, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        calls.append(path)
        # Toda chamada de loja precisa vir assinada corretamente
        base = f"{PARTNER_ID}{path}{params['timestamp']}{ACCESS_TOKEN}{SHOP_ID}"
        assert params["sign"] == expected_sign(base)

        if path == "/api/v2/product/get_item_list":
            assert params.get_list("item_status") == ["NORMAL", "UNLIST"]
            return httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "item": [{"item_id": 1001}, {"item_id": 2002}],
                        "total_count": 2,
                        "has_next_page": False,
                        "next_offset": 2,
                    },
                },
            )
        if path == "/api/v2/product/get_item_base_info":
            assert params["item_id_list"] == "1001,2002"
            return httpx.Response(
                200,
                json={
                    "error": "",
                    "response": {
                        "item_list": [ITEM_SIMPLE, ITEM_WITH_MODELS],
                    },
                },
            )
        if path == "/api/v2/product/get_model_list":
            assert params["item_id"] == "2002"
            return httpx.Response(200, json={"error": "", "response": MODELS_2002})
        return httpx.Response(404, json={"error": "not_found"})

    install_fake_shopee(monkeypatch, handler)

    page = await service.list_item_ids(credential, 0, 20)
    assert page["item_ids"] == [1001, 2002]
    items = await service.fetch_items_with_models(credential, page["item_ids"])
    rows = service.to_stock_rows(credential, items)

    assert [r["shopee_sku"] for r in rows] == ["CAM-001", "TEN-40", "TEN-41"]
    # get_model_list só é chamado para o item com variações
    assert calls.count("/api/v2/product/get_model_list") == 1


@pytest.mark.anyio
async def test_shopee_error_in_200_response_is_raised(service, credential, monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "error": "error_auth",
                "message": "Invalid access_token.",
                "request_id": "r1",
            },
        )

    install_fake_shopee(monkeypatch, handler)
    with pytest.raises(APIError) as exc:
        await service.list_item_ids(credential, 0, 20)
    assert exc.value.status_code == 401
    assert exc.value.code == "shopee_unauthorized"
    assert exc.value.details["request_id"] == "r1"


@pytest.mark.anyio
async def test_exchange_code_sends_signed_public_request(service, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/auth/token/get"
        ts = request.url.params["timestamp"]
        assert request.url.params["sign"] == expected_sign(
            f"{PARTNER_ID}/api/v2/auth/token/get{ts}"
        )
        import json

        body = json.loads(request.content)
        assert body == {"code": "the-code", "shop_id": SHOP_ID, "partner_id": int(PARTNER_ID)}
        return httpx.Response(
            200,
            json={
                "error": "",
                "access_token": "new",
                "refresh_token": "ref",
                "expire_in": 14400,
            },
        )

    install_fake_shopee(monkeypatch, handler)
    data = await service.exchange_code_for_tokens("the-code", SHOP_ID)
    assert data["access_token"] == "new"
