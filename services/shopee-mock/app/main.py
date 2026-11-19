"""Simulador da Shopee Open Platform v2 para testes locais do Stock Sync.

Imita os endpoints usados pelo integrations-service, com as mesmas regras de
assinatura (HMAC-SHA256), validade de timestamp, code de uso único e tokens com
expiração. NÃO é a Shopee real: serve para validar o fluxo de ponta a ponta.

Páginas para humanos:
- ``/``                                  painel da loja fictícia (editar estoque)
- ``/api/v2/shop/auth_partner``          tela de autorização da loja
- ``/product/{shop_id}/{item_id}``       "página pública" do anúncio
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from html import escape
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import data

PARTNER_ID = os.getenv("MOCK_PARTNER_ID", "100001")
PARTNER_KEY = os.getenv("MOCK_PARTNER_KEY", "mock-partner-key-stock-sync")
PUBLIC_URL = os.getenv("MOCK_PUBLIC_URL", "http://localhost:8090").rstrip("/")

TIMESTAMP_TOLERANCE = 300  # segundos, como na Shopee
CODE_TTL = 600  # code de autorização: 10 minutos, uso único
ACCESS_TTL = 4 * 3600  # access token: 4 horas
REFRESH_TTL = 30 * 24 * 3600  # refresh token: 30 dias

app = FastAPI(title="Shopee Open Platform (simulador)", version="1.0.0")

_codes: dict[str, dict[str, Any]] = {}


# =============================================================================
# Utilidades: respostas, assinatura e tokens
# =============================================================================


def _request_id() -> str:
    return uuid.uuid4().hex


def ok(payload: dict[str, Any] | None = None, **top_level: Any) -> JSONResponse:
    body: dict[str, Any] = {"error": "", "message": "", "warning": "", "request_id": _request_id()}
    if payload is not None:
        body["response"] = payload
    body.update(top_level)
    return JSONResponse(body)


def fail(error: str, message: str, status: int = 403) -> JSONResponse:
    return JSONResponse(
        {"error": error, "message": message, "request_id": _request_id()}, status_code=status
    )


def _hmac(message: str) -> str:
    return hmac.new(PARTNER_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()


def check_common(
    request: Request, *, shop_level: bool
) -> tuple[JSONResponse | None, dict[str, Any] | None]:
    """Valida partner_id, timestamp e sign. Para APIs de loja, valida também o token."""
    q = request.query_params
    path = request.url.path
    if q.get("partner_id") != PARTNER_ID:
        return fail("error_param", "Invalid partner_id."), None
    try:
        timestamp = int(q.get("timestamp", ""))
    except ValueError:
        return fail("error_param", "Invalid timestamp."), None
    if abs(time.time() - timestamp) > TIMESTAMP_TOLERANCE:
        return fail("error_param", "Timestamp expired. Check your server clock."), None

    base = f"{PARTNER_ID}{path}{timestamp}"
    token_data = None
    if shop_level:
        access_token, shop_id = q.get("access_token", ""), q.get("shop_id", "")
        if not access_token or not shop_id:
            return fail("error_param", "access_token and shop_id are required."), None
        base += f"{access_token}{shop_id}"

    if not hmac.compare_digest(q.get("sign", ""), _hmac(base)):
        return fail("error_sign", "Wrong sign."), None

    if shop_level:
        token_data = decode_token(q["access_token"], "access")
        if token_data is None or str(token_data["shop_id"]) != q["shop_id"]:
            return fail("invalid_access_token", "Invalid access_token."), None
    return None, token_data


def make_token(kind: str, shop_id: int, ttl: int) -> str:
    """Token assinado e sem estado: continua válido mesmo se o simulador reiniciar."""
    payload = {"k": kind, "s": shop_id, "e": int(time.time()) + ttl, "n": secrets.token_hex(4)}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{raw}.{_hmac(raw)[:24]}"


def decode_token(token: str, kind: str) -> dict[str, Any] | None:
    try:
        raw, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _hmac(raw)[:24]):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("k") != kind or payload.get("e", 0) < time.time():
        return None
    return {"shop_id": payload["s"], "expires_at": payload["e"]}


def token_pair(shop_id: int) -> dict[str, Any]:
    return {
        "access_token": make_token("access", shop_id, ACCESS_TTL),
        "refresh_token": make_token("refresh", shop_id, REFRESH_TTL),
        "expire_in": ACCESS_TTL,
    }


def image_url(item_id: int) -> str:
    return f"{PUBLIC_URL}/img/{item_id}.svg"


def price_info(price: float, original: float) -> list[dict[str, Any]]:
    return [
        {
            "current_price": price,
            "original_price": original,
            "inflated_price_of_current_price": price,
            "inflated_price_of_original_price": original,
            "currency": "BRL",
        }
    ]


# =============================================================================
# Autorização (OAuth)
# =============================================================================


@app.get("/api/v2/shop/auth_partner", response_class=HTMLResponse)
def auth_partner(request: Request):
    error, _ = check_common(request, shop_level=False)
    if error:
        return error
    redirect = request.query_params.get("redirect")
    if not redirect:
        return fail("error_param", "redirect is required.")

    return HTMLResponse(
        _page(
            "Autorizar loja",
            f"""
            <div class="card narrow">
              <div class="brand">Shopee <span>Open Platform · Simulador</span></div>
              <h1>Autorizar acesso à loja</h1>
              <p>O app <b>Stock Sync</b> (partner_id {escape(PARTNER_ID)}) quer acessar a loja:</p>
              <div class="shop">
                <div class="avatar">LD</div>
                <div><b>{escape(data.SHOP_INFO["shop_name"])}</b><br>
                <small>shop_id {data.SHOP_ID} · região {data.SHOP_INFO["region"]}</small></div>
              </div>
              <p class="muted">Permissões: produtos, estoque e informações da loja.</p>
              <form method="post" action="/_mock/authorize">
                <input type="hidden" name="redirect" value="{escape(redirect)}">
                <button class="primary" name="decision" value="allow">Autorizar</button>
                <button class="ghost" name="decision" value="deny">Cancelar</button>
              </form>
              <p class="note">Ambiente simulado. Nenhum dado é enviado à Shopee.</p>
            </div>""",
        )
    )


def _append_query(url: str, params: dict[str, Any]) -> str:
    parts = urlsplit(url)
    query = f"{parts.query}&{urlencode(params)}" if parts.query else urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


@app.post("/_mock/authorize")
def authorize(redirect: str = Form(...), decision: str = Form(...)):
    if decision != "allow":
        return RedirectResponse(_append_query(redirect, {"error": "access_denied"}), 302)
    code = secrets.token_hex(16)
    _codes[code] = {"shop_id": data.SHOP_ID, "expires_at": time.time() + CODE_TTL}
    return RedirectResponse(_append_query(redirect, {"code": code, "shop_id": data.SHOP_ID}), 302)


@app.post("/api/v2/auth/token/get")
async def token_get(request: Request):
    error, _ = check_common(request, shop_level=False)
    if error:
        return error
    body = await request.json()
    if str(body.get("partner_id")) != PARTNER_ID:
        return fail("error_param", "partner_id in body does not match.")

    entry = _codes.pop(body.get("code", ""), None)  # uso único
    if not entry or entry["expires_at"] < time.time():
        return fail("error_auth", "Invalid code.")
    if entry["shop_id"] != body.get("shop_id"):
        return fail("error_auth", "code does not belong to this shop_id.")

    return ok(
        None,
        **token_pair(entry["shop_id"]),
        partner_id=int(PARTNER_ID),
        shop_id_list=[entry["shop_id"]],
        merchant_id_list=[],
    )


@app.post("/api/v2/auth/access_token/get")
async def access_token_get(request: Request):
    error, _ = check_common(request, shop_level=False)
    if error:
        return error
    body = await request.json()
    token = decode_token(body.get("refresh_token", ""), "refresh")
    if token is None or token["shop_id"] != body.get("shop_id"):
        return fail("error_auth", "Invalid refresh_token.")
    return ok(
        None, **token_pair(token["shop_id"]), partner_id=int(PARTNER_ID), shop_id=token["shop_id"]
    )


# =============================================================================
# Loja e produtos
# =============================================================================


@app.get("/api/v2/shop/get_shop_info")
def get_shop_info(request: Request):
    error, token = check_common(request, shop_level=True)
    if error:
        return error
    return ok(
        None,
        **data.SHOP_INFO,
        auth_time=int(time.time()),
        expire_time=token["expires_at"] + REFRESH_TTL,
    )


@app.get("/api/v2/product/get_item_list")
def get_item_list(request: Request):
    error, _ = check_common(request, shop_level=True)
    if error:
        return error
    q = request.query_params
    statuses = q.getlist("item_status")
    if not statuses:
        return fail("error_param", "item_status is required.", 400)
    try:
        offset = int(q.get("offset", 0))
        page_size = int(q.get("page_size", 10))
    except ValueError:
        return fail("error_param", "offset/page_size must be integers.", 400)
    if not 1 <= page_size <= 100:
        return fail("error_param", "page_size must be between 1 and 100.", 400)

    matching = sorted(
        (i for i in data.ITEMS.values() if i["item_status"] in statuses),
        key=lambda i: i["item_id"],
    )
    page = matching[offset : offset + page_size]
    has_next = offset + page_size < len(matching)
    return ok(
        {
            "item": [
                {
                    "item_id": i["item_id"],
                    "item_status": i["item_status"],
                    "update_time": i["update_time"],
                }
                for i in page
            ],
            "total_count": len(matching),
            "has_next_page": has_next,
            "next_offset": offset + len(page),
        }
    )


@app.get("/api/v2/product/get_item_base_info")
def get_item_base_info(request: Request):
    error, _ = check_common(request, shop_level=True)
    if error:
        return error
    raw = request.query_params.get("item_id_list", "")
    try:
        ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return fail("error_param", "item_id_list must be comma separated integers.", 400)
    if not ids or len(ids) > 50:
        return fail("error_param", "item_id_list must have between 1 and 50 ids.", 400)

    items = []
    for item_id in ids:
        item = data.ITEMS.get(item_id)
        if not item:
            continue
        has_model = bool(item["_models"])
        out: dict[str, Any] = {
            "item_id": item["item_id"],
            "category_id": item["category_id"],
            "item_name": item["item_name"],
            "item_sku": item["item_sku"],
            "create_time": item["create_time"],
            "update_time": item["update_time"],
            "image": {"image_url_list": [image_url(item_id)], "image_id_list": [f"img{item_id}"]},
            "item_status": item["item_status"],
            "has_model": has_model,
            "condition": item["condition"],
            "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
        }
        # Como na Shopee: itens com variação não trazem preço/estoque no nível do item
        if not has_model:
            out["price_info"] = price_info(item["_price"], item["_original"])
            out["stock_info_v2"] = data.stock_info_v2(item["_stock"], item["_reserved"])
        items.append(out)
    return ok({"item_list": items})


@app.get("/api/v2/product/get_model_list")
def get_model_list(request: Request):
    error, _ = check_common(request, shop_level=True)
    if error:
        return error
    try:
        item = data.ITEMS.get(int(request.query_params.get("item_id", "")))
    except ValueError:
        item = None
    if not item:
        return fail("error_item_not_found", "Item not found.", 404)

    return ok(
        {
            "tier_variation": item["_tiers"],
            "model": [
                {
                    "model_id": m["model_id"],
                    "tier_index": m["tier_index"],
                    "model_sku": m["model_sku"],
                    "model_status": m["model_status"],
                    "price_info": m["price_info"],
                    "stock_info_v2": data.stock_info_v2(m["_stock"], m["_reserved"]),
                }
                for m in item["_models"]
            ],
        }
    )


@app.post("/api/v2/product/update_stock")
async def update_stock(request: Request):
    """Mesmo formato da Shopee — útil quando o Stock Sync passar a escrever estoque."""
    error, _ = check_common(request, shop_level=True)
    if error:
        return error
    body = await request.json()
    item = data.ITEMS.get(body.get("item_id"))
    if not item:
        return fail("error_item_not_found", "Item not found.", 404)

    success, failures = [], []
    for entry in body.get("stock_list") or []:
        stock = sum(int(s.get("stock", 0)) for s in entry.get("seller_stock") or [])
        model_id = entry.get("model_id") or 0
        target = data.find_model(item, model_id) if model_id else item
        if target is None or stock < 0:
            failures.append({"model_id": model_id, "failed_reason": "invalid model or stock"})
            continue
        target["_stock"] = stock
        success.append({"model_id": model_id, "seller_stock": [{"stock": stock}]})
    data.touch(item)
    return ok({"success_list": success, "failure_list": failures})


# =============================================================================
# Painel do simulador (para humanos)
# =============================================================================

STYLE = """
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  background: #f5f5f5; color: #222; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 24px 16px; }
.card { background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
  padding: 24px; margin-bottom: 16px; }
.narrow { max-width: 440px; margin: 48px auto; }
.brand { color: #ee4d2d; font-weight: 700; font-size: 20px; margin-bottom: 16px; }
.brand span { color: #666; font-weight: 400; font-size: 14px; }
h1 { font-size: 20px; margin: 0 0 12px; }
.shop { display: flex; gap: 12px; align-items: center; padding: 12px; background: #fff5f2;
  border-radius: 8px; margin: 12px 0; }
.avatar { width: 44px; height: 44px; border-radius: 50%; background: #ee4d2d; color: #fff;
  display: grid; place-items: center; font-weight: 700; }
button { border: 0; border-radius: 6px; padding: 10px 18px; font-size: 14px; cursor: pointer; }
.primary { background: #ee4d2d; color: #fff; }
.ghost { background: transparent; color: #555; }
.small { padding: 6px 10px; font-size: 13px; }
.muted, small { color: #666; }
.note { margin-top: 20px; font-size: 12px; color: #999; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; vertical-align: middle; }
th { font-size: 12px; text-transform: uppercase; color: #777; }
input[type=number] { width: 80px; padding: 6px; border: 1px solid #ccc; border-radius: 6px; }
select { padding: 6px; border-radius: 6px; border: 1px solid #ccc; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 12px;
  background: #eee; }
.NORMAL { background: #dcfce7; color: #166534; } .UNLIST { background: #fef3c7; color: #92400e; }
.BANNED { background: #fee2e2; color: #991b1b; }
code { background: #f1f1f1; padding: 1px 5px; border-radius: 4px; }
img.thumb { width: 36px; height: 36px; border-radius: 6px; vertical-align: middle; }
"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · Shopee simulador</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def _stock_form(item_id: int, model_id: int, stock: int) -> str:
    return (
        f"<form method='post' action='/_mock/stock' style='display:flex;gap:6px'>"
        f"<input type='hidden' name='item_id' value='{item_id}'>"
        f"<input type='hidden' name='model_id' value='{model_id}'>"
        f"<input type='number' name='stock' min='0' value='{stock}'>"
        f"<button class='primary small'>Salvar</button></form>"
    )


@app.get("/", response_class=HTMLResponse)
def dashboard():
    rows = []
    for item in sorted(data.ITEMS.values(), key=lambda i: i["item_id"]):
        status_select = (
            f"<form method='post' action='/_mock/status' style='display:inline'>"
            f"<input type='hidden' name='item_id' value='{item['item_id']}'>"
            f"<select name='status' onchange='this.form.submit()'>"
            + "".join(
                f"<option {'selected' if s == item['item_status'] else ''}>{s}</option>"
                for s in ("NORMAL", "UNLIST", "BANNED", "REVIEWING")
            )
            + "</select></form>"
        )
        head = (
            f"<td><img class='thumb' src='/img/{item['item_id']}.svg' alt=''> "
            f"<b>{escape(item['item_name'])}</b><br><small>item_id {item['item_id']}</small></td>"
            f"<td>{status_select}</td>"
        )
        if not item["_models"]:
            rows.append(
                f"<tr>{head}<td>—</td><td><code>{escape(item['item_sku'] or 'sem SKU')}</code></td>"
                f"<td>R$ {item['_price']:.2f}</td><td>{item['_reserved']}</td>"
                f"<td>{_stock_form(item['item_id'], 0, item['_stock'])}</td></tr>"
            )
            continue
        for index, model in enumerate(item["_models"]):
            option = " / ".join(
                item["_tiers"][t]["option_list"][o]["option"]
                for t, o in enumerate(model["tier_index"])
            )
            price = model["price_info"][0]["current_price"]
            rows.append(
                f"<tr>{head if index == 0 else '<td></td><td></td>'}"
                f"<td>{escape(option)}</td><td><code>{escape(model['model_sku'])}</code></td>"
                f"<td>R$ {price:.2f}</td><td>{model['_reserved']}</td>"
                f"<td>{_stock_form(item['item_id'], model['model_id'], model['_stock'])}</td></tr>"
            )

    body = f"""
    <div class="wrap">
      <div class="card">
        <div class="brand">Shopee <span>Open Platform · Simulador</span></div>
        <h1>{escape(data.SHOP_INFO["shop_name"])} <small>(shop_id {data.SHOP_ID})</small></h1>
        <p class="muted">Altere o estoque ou o status aqui e clique em <b>Sincronizar</b> no
        Stock Sync para ver a mudança. Os dados voltam ao padrão quando o simulador reinicia.</p>
        <p class="muted">partner_id <code>{escape(PARTNER_ID)}</code> ·
        partner_key <code>{escape(PARTNER_KEY)}</code></p>
        <form method="post" action="/_mock/reset">
          <button class="ghost small">Restaurar dados padrão</button></form>
      </div>
      <div class="card" style="overflow-x:auto">
        <table>
          <thead><tr><th>Anúncio</th><th>Status</th><th>Variação</th><th>SKU</th>
          <th>Preço</th><th>Reservado</th><th>Estoque disponível</th></tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
    </div>"""
    return HTMLResponse(_page("Painel", body))


@app.post("/_mock/stock")
def set_stock(item_id: int = Form(...), model_id: int = Form(0), stock: int = Form(...)):
    item = data.ITEMS.get(item_id)
    if item:
        target = data.find_model(item, model_id) if model_id else item
        if target is not None:
            target["_stock"] = max(0, stock)
            data.touch(item)
    return RedirectResponse("/", 303)


@app.post("/_mock/status")
def set_status(item_id: int = Form(...), status: str = Form(...)):
    item = data.ITEMS.get(item_id)
    if item and status in ("NORMAL", "UNLIST", "BANNED", "REVIEWING"):
        item["item_status"] = status
        data.touch(item)
    return RedirectResponse("/", 303)


@app.post("/_mock/reset")
def reset_data():
    data.reset()
    return RedirectResponse("/", 303)


@app.get("/product/{shop_id}/{item_id}", response_class=HTMLResponse)
def product_page(shop_id: int, item_id: int):
    item = data.ITEMS.get(item_id)
    if not item or shop_id != data.SHOP_ID:
        return HTMLResponse(
            _page("Não encontrado", "<div class='card narrow'>Anúncio não " "encontrado.</div>"),
            status_code=404,
        )
    return HTMLResponse(
        _page(
            item["item_name"],
            f"""<div class="card narrow">
              <div class="brand">Shopee <span>· Simulador</span></div>
              <img src="/img/{item_id}.svg" alt="" style="width:100%;border-radius:8px">
              <h1 style="margin-top:16px">{escape(item["item_name"])}</h1>
              <p class="muted">Vendido por {escape(data.SHOP_INFO["shop_name"])}</p>
              <span class="pill {item["item_status"]}">{item["item_status"]}</span>
              <p class="note"><a href="/">Abrir painel do simulador</a></p>
            </div>""",
        )
    )


@app.get("/img/{item_id}.svg")
def item_image(item_id: int):
    item = data.ITEMS.get(item_id)
    name = item["item_name"] if item else "?"
    initials = "".join(w[0] for w in name.split()[:2]).upper()
    color = data.COLORS[item_id % len(data.COLORS)]
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200' viewBox='0 0 200 200'>"
        f"<rect width='200' height='200' fill='{color}'/>"
        f"<text x='100' y='118' font-family='Arial,sans-serif' font-size='64' font-weight='700' "
        f"fill='white' text-anchor='middle'>{escape(initials)}</text></svg>"
    )
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.get("/health")
def health():
    return {"status": "ok", "app": "shopee-mock", "items": len(data.ITEMS)}
