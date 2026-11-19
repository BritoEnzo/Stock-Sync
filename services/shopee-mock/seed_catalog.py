"""Popula o catálogo do Stock Sync com produtos/SKUs que correspondem à loja do simulador.

Alguns estoques são propositalmente diferentes dos da "Shopee" para demonstrar a
coluna de diferença, e a Mochila é vinculada por PlatformMapping (SKU interno diferente
do SKU da Shopee). Pode ser executado mais de uma vez: o que já existe é ignorado.

Uso:  python services/shopee-mock/seed_catalog.py [URL do catalog-service]
"""

import json
import sys
import urllib.error
import urllib.request

CATALOG = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001").rstrip("/")

# produto -> [(sku interno, preço, estoque no Stock Sync, (item_id, model_id) p/ mapping)]
SEED = {
    "[Demo] Camiseta Básica Algodão": [
        ("CAM-BAS-P", 49.9, 15, None),
        ("CAM-BAS-M", 49.9, 20, None),  # Shopee: 22 -> diferença +2
        ("CAM-BAS-G", 49.9, 8, None),
    ],
    "[Demo] Tênis Corrida Pro": [
        ("TEN-PRO-39", 299.9, 4, None),
        ("TEN-PRO-40", 299.9, 2, None),  # Shopee: 0 -> diferença -2
        ("TEN-PRO-41", 299.9, 7, None),
        ("TEN-PRO-42", 299.9, 3, None),
    ],
    "[Demo] Caneca Térmica 500ml": [("CAN-TER-500", 89.9, 40, None)],
    "[Demo] Fone Bluetooth X2": [("FONE-BT-X2", 159.9, 10, None)],  # Shopee: 12 -> +2
    "[Demo] Mochila Urbana 25L": [
        ("MOCHILA-PRETA", 189.9, 6, ("1005", "10051", "MOC-URB-PRE")),
        ("MOCHILA-CINZA", 189.9, 2, ("1005", "10052", "MOC-URB-CIN")),
    ],
    "[Demo] Garrafa Squeeze 750ml": [("GAR-SQZ-750", 34.9, 5, None)],  # Shopee: 0 -> -5
}


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{CATALOG}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"null")


def main() -> None:
    try:
        products = {p["name"]: p for p in call("GET", "/api/v1/products")}
        skus = {s["internal_sku"]: s for s in call("GET", "/api/v1/skus")}
        mappings = call("GET", "/api/v1/platform-mappings")
    except urllib.error.URLError as exc:
        sys.exit(f"Não foi possível acessar o catalog-service em {CATALOG}: {exc}")

    mapped = {(m["platform"], m["platform_product_id"], m["platform_variant_id"]) for m in mappings}
    created = {"produtos": 0, "skus": 0, "mapeamentos": 0}

    for name, entries in SEED.items():
        product = products.get(name)
        if product is None:
            product = call(
                "POST",
                "/api/v1/products",
                {
                    "name": name,
                    "description": "Produto de demonstração (simulador Shopee)",
                },
            )
            created["produtos"] += 1

        for internal_sku, price, stock, mapping in entries:
            sku = skus.get(internal_sku)
            if sku is None:
                sku = call(
                    "POST",
                    "/api/v1/skus",
                    {
                        "product_id": product["id"],
                        "internal_sku": internal_sku,
                        "price": price,
                        "stock_quantity": stock,
                    },
                )
                created["skus"] += 1

            if mapping and ("shopee", mapping[0], mapping[1]) not in mapped:
                item_id, model_id, shopee_sku = mapping
                call(
                    "POST",
                    "/api/v1/platform-mappings",
                    {
                        "sku_id": sku["id"],
                        "platform": "shopee",
                        "platform_sku": shopee_sku,
                        "platform_product_id": item_id,
                        "platform_variant_id": model_id,
                        "is_active": True,
                    },
                )
                created["mapeamentos"] += 1

    print(
        "Catálogo de demonstração pronto:",
        ", ".join(f"{v} {k}" for k, v in created.items()),
        "criados.",
    )


if __name__ == "__main__":
    main()
