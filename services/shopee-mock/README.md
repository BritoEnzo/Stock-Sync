# shopee-mock

Simulador local da **Shopee Open Platform v2**, para testar a integração Shopee do
Stock Sync sem conta de desenvolvedor nem loja real.

Ele imita os endpoints que o `integrations-service` usa, com as mesmas regras da Shopee:
assinatura HMAC-SHA256, validade de 5 minutos no timestamp, `code` de autorização de uso
único (10 min), access token de 4h e refresh token de 30 dias.

| Endpoint | O que faz |
|---|---|
| `GET /api/v2/shop/auth_partner` | Tela de autorização da loja fictícia |
| `POST /api/v2/auth/token/get` | Troca o `code` por tokens |
| `POST /api/v2/auth/access_token/get` | Renova o access token |
| `GET /api/v2/shop/get_shop_info` | Dados da loja |
| `GET /api/v2/product/get_item_list` | Lista de anúncios (paginada, filtro por status) |
| `GET /api/v2/product/get_item_base_info` | Detalhes, preço e estoque dos anúncios |
| `GET /api/v2/product/get_model_list` | Variações com preço e estoque |
| `POST /api/v2/product/update_stock` | Atualiza o estoque |

O **painel** em http://localhost:8090 mostra a loja fictícia e permite alterar o estoque
e o status dos anúncios. Os dados ficam em memória e voltam ao padrão quando o container
reinicia (os tokens continuam válidos, porque são assinados e não dependem de memória).

## Como usar

Crie um `.env` na raiz do repositório:

```
SHOPEE_PARTNER_ID=100001
SHOPEE_PARTNER_KEY=mock-partner-key-stock-sync
SHOPEE_API_HOST=http://shopee-mock:8000
SHOPEE_AUTH_HOST=http://localhost:8090
SHOPEE_SHOP_URL=http://localhost:8090
```

Depois rode `docker compose up -d --build` e use **Integrações → Conectar Shopee**.

Para popular o catálogo com SKUs que batem com a loja fictícia (e ver a comparação de
estoque funcionando):

```
python services/shopee-mock/seed_catalog.py
```

Para voltar à Shopee real, troque essas variáveis pelas credenciais do app no
Shopee Open Platform Console e remova `SHOPEE_AUTH_HOST` e `SHOPEE_SHOP_URL`.
