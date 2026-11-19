"""Loja fictícia do simulador. Os dados ficam em memória e voltam ao padrão ao reiniciar."""

import copy
import time

SHOP_ID = 100200300
SHOP_INFO = {
    "shop_name": "Loja Demo Stock Sync",
    "region": "BR",
    "status": "NORMAL",
    "is_cb": False,
    "is_sip": False,
    "is_upgraded_cbsc": False,
    "merchant_id": None,
    "shop_fulfillment_flag": "Others",
    "is_main_shop": False,
    "is_direct_shop": False,
}

COLORS = ["#ee4d2d", "#2563eb", "#059669", "#7c3aed", "#d97706", "#0891b2", "#db2777", "#4b5563"]


def _model(model_id, option_index, sku, price, stock, reserved=0, original=None):
    return {
        "model_id": model_id,
        "tier_index": option_index,
        "model_sku": sku,
        "model_status": "MODEL_NORMAL",
        "price_info": [
            {
                "current_price": price,
                "original_price": original or price,
                "inflated_price_of_current_price": price,
                "inflated_price_of_original_price": original or price,
                "currency": "BRL",
            }
        ],
        "_stock": stock,
        "_reserved": reserved,
    }


def _item(
    item_id,
    name,
    sku,
    status,
    category_id,
    *,
    price=None,
    stock=0,
    reserved=0,
    original=None,
    tiers=None,
    models=None,
):
    return {
        "item_id": item_id,
        "item_name": name,
        "item_sku": sku,
        "item_status": status,
        "category_id": category_id,
        "condition": "NEW",
        "create_time": 1767225600,  # 2026-01-01
        "update_time": 1767225600,
        "_price": price,
        "_original": original or price,
        "_stock": stock,
        "_reserved": reserved,
        "_tiers": tiers or [],
        "_models": models or [],
    }


_INITIAL_ITEMS = [
    _item(
        1001,
        "Camiseta Básica Algodão",
        "",
        "NORMAL",
        100017,
        tiers=[
            {"name": "Tamanho", "option_list": [{"option": "P"}, {"option": "M"}, {"option": "G"}]}
        ],
        models=[
            _model(10011, [0], "CAM-BAS-P", 49.9, 15),
            _model(10012, [1], "CAM-BAS-M", 49.9, 22, reserved=3),
            _model(10013, [2], "CAM-BAS-G", 49.9, 8),
        ],
    ),
    _item(
        1002,
        "Tênis Corrida Pro",
        "",
        "NORMAL",
        100635,
        tiers=[
            {"name": "Número", "option_list": [{"option": o} for o in ("39", "40", "41", "42")]}
        ],
        models=[
            _model(10021, [0], "TEN-PRO-39", 299.9, 4, original=349.9),
            _model(10022, [1], "TEN-PRO-40", 299.9, 0, original=349.9),
            _model(10023, [2], "TEN-PRO-41", 299.9, 7, reserved=1, original=349.9),
            _model(10024, [3], "TEN-PRO-42", 299.9, 3, original=349.9),
        ],
    ),
    _item(
        1003,
        "Caneca Térmica 500ml",
        "CAN-TER-500",
        "NORMAL",
        100636,
        price=89.9,
        stock=40,
        reserved=2,
    ),
    _item(
        1004,
        "Fone Bluetooth X2",
        "FONE-BT-X2",
        "NORMAL",
        100535,
        price=159.9,
        stock=12,
        original=199.9,
    ),
    _item(
        1005,
        "Mochila Urbana 25L",
        "",
        "UNLIST",
        100637,
        tiers=[{"name": "Cor", "option_list": [{"option": "Preta"}, {"option": "Cinza"}]}],
        models=[
            _model(10051, [0], "MOC-URB-PRE", 189.9, 6),
            _model(10052, [1], "MOC-URB-CIN", 189.9, 2),
        ],
    ),
    _item(1006, "Garrafa Squeeze 750ml", "GAR-SQZ-750", "NORMAL", 100636, price=34.9, stock=0),
    _item(1007, "Kit Meias Esportivas (3 pares)", "", "NORMAL", 100017, price=39.9, stock=30),
    _item(1008, "Relógio Digital Sport", "REL-DIG-01", "BANNED", 100535, price=119.9, stock=5),
]

ITEMS: dict[int, dict] = {}


def reset() -> None:
    ITEMS.clear()
    for item in copy.deepcopy(_INITIAL_ITEMS):
        ITEMS[item["item_id"]] = item


reset()


def find_model(item: dict, model_id: int) -> dict | None:
    return next((m for m in item["_models"] if m["model_id"] == model_id), None)


def touch(item: dict) -> None:
    item["update_time"] = int(time.time())


def stock_info_v2(stock: int, reserved: int) -> dict:
    return {
        "summary_info": {"total_reserved_stock": reserved, "total_available_stock": stock},
        "seller_stock": [{"location_id": "BRZ", "stock": stock, "if_saleable": True}],
        "shopee_stock": [],
    }
