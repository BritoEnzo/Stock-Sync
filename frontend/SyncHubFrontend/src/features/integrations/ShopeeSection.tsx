import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ExternalLink, Loader2, Plus, RefreshCw, Store, Trash2 } from "lucide-react";
import { Card, CardBody, CardHeader } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Badge } from "../../components/ui/Badge";
import { EmptyState } from "../../components/ui/EmptyState";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Table, type Column } from "../../components/ui/Table";
import { useToast } from "../../components/ui/toast-context";
import { formatCurrency } from "../../lib/format";
import {
  integrationsClient,
  type ShopeeCredentialResponse,
  type ShopeeStockResponse,
  type ShopeeStockRow,
} from "../../lib/api/integrations-client";

const PAGE_SIZE = 20;

const CALLBACK_ERRORS: Record<string, string> = {
  "shopee_not_configured": "Integração Shopee não configurada no servidor (Partner ID/Key).",
  "shopee_unauthorized": "A Shopee recusou a autorização. Tente novamente.",
  "unauthorized": "O link de autorização expirou ou é inválido. Tente novamente.",
  "conflict": "Esta loja já está conectada a outra conta.",
  "main_account_not_supported": "Autorize uma loja específica, não a conta principal (CNSC).",
};

function stockTone(row: ShopeeStockRow): "success" | "warning" | "danger" | "neutral" {
  if (row.stock_difference === null) {return "neutral";}
  if (row.stock_difference === 0) {return "success";}
  return row.stock_difference > 0 ? "danger" : "warning";
}

const columns: Column<ShopeeStockRow>[] = [
  {
    key: "item",
    header: "Anúncio",
    render: (row) => (
      <div className="flex items-center gap-3">
        {row.image_url ? (
          <img src={row.image_url} alt="" className="h-10 w-10 rounded-lg object-cover" />
        ) : (
          <div className="h-10 w-10 rounded-lg bg-slate-100" />
        )}
        <div className="min-w-0">
          <a
            href={row.permalink}
            target="_blank"
            rel="noopener noreferrer"
            className="block max-w-xs truncate font-medium text-slate-900 hover:underline"
          >
            {row.item_name ?? `Item ${row.item_id}`}
          </a>
          <p className="text-xs text-slate-500">
            {row.variation_name ? `${row.variation_name} · ` : ""}
            {row.shopee_sku ?? "sem SKU"}
          </p>
        </div>
      </div>
    ),
  },
  {
    key: "status",
    header: "Status",
    render: (row) => (
      <Badge tone={row.item_status === "NORMAL" ? "success" : "neutral"}>
        {row.item_status ?? "—"}
      </Badge>
    ),
  },
  { key: "price", header: "Preço", align: "right", render: (row) => formatCurrency(row.price) },
  {
    key: "shopee_stock",
    header: "Estoque Shopee",
    align: "right",
    render: (row) => (
      <span>
        {row.available_stock}
        {row.reserved_stock > 0 && (
          <span className="ml-1 text-xs text-slate-400">(+{row.reserved_stock} reserv.)</span>
        )}
      </span>
    ),
  },
  {
    key: "local_stock",
    header: "Estoque Stock Sync",
    align: "right",
    render: (row) =>
      row.local ? (
        <span title={`SKU ${row.local.internal_sku}`}>{row.local.stock_quantity}</span>
      ) : (
        <span className="text-xs text-slate-400">não vinculado</span>
      ),
  },
  {
    key: "diff",
    header: "Diferença",
    align: "right",
    render: (row) => (
      <Badge tone={stockTone(row)}>
        {row.stock_difference === null
          ? "—"
          : row.stock_difference > 0
            ? `+${row.stock_difference}`
            : row.stock_difference}
      </Badge>
    ),
  },
];

export function ShopeeSection() {
  const { notify } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [credentials, setCredentials] = useState<ShopeeCredentialResponse[]>([]);
  const [stock, setStock] = useState<ShopeeStockResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingStock, setIsLoadingStock] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const connected = credentials.some((c) => c.is_active);

  const loadCredentials = useCallback(async () => {
    try {
      setCredentials(await integrationsClient.listShopeeCredentials());
    } catch {
      notify({ tone: "error", title: "Erro ao carregar conta Shopee" });
    } finally {
      setIsLoading(false);
    }
  }, [notify]);

  const loadStock = useCallback(
    async (nextOffset: number) => {
      setIsLoadingStock(true);
      try {
        setStock(await integrationsClient.getShopeeStock({ offset: nextOffset, limit: PAGE_SIZE }));
        setOffset(nextOffset);
      } catch (error) {
        const message = error instanceof Error ? error.message : undefined;
        notify({ tone: "error", title: "Erro ao buscar estoque da Shopee", description: message });
      } finally {
        setIsLoadingStock(false);
      }
    },
    [notify]
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- carga inicial assíncrona
    loadCredentials();
  }, [loadCredentials]);

  useEffect(() => {
    if (connected) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- carga assíncrona ao conectar
      loadStock(0);
    }
  }, [connected, loadStock]);

  // Resultado do retorno do OAuth (?shopee=connected|error&reason=...)
  useEffect(() => {
    const result = searchParams.get("shopee");
    if (!result) {return;}
    if (result === "connected") {
      notify({ tone: "success", title: "Loja Shopee conectada" });
    } else {
      const reason = searchParams.get("reason") ?? "";
      notify({
        tone: "error",
        title: "Não foi possível conectar a Shopee",
        description: CALLBACK_ERRORS[reason] ?? reason,
      });
    }
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, notify]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const { auth_url: authUrl } = await integrationsClient.getShopeeAuthUrl();
      window.location.href = authUrl;
    } catch (error) {
      const message = error instanceof Error ? error.message : undefined;
      notify({ tone: "error", title: "Erro ao iniciar conexão com a Shopee", description: message });
      setConnecting(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await integrationsClient.syncShopeeStock();
      notify({
        tone: "success",
        title: `${result.items_synced} anúncios sincronizados`,
        description: `${result.skus_matched}/${result.skus_found} SKUs vinculados · ${result.skus_with_divergence} com divergência`,
      });
      loadStock(offset);
    } catch (error) {
      const message = error instanceof Error ? error.message : undefined;
      notify({ tone: "error", title: "Erro ao sincronizar", description: message });
    } finally {
      setSyncing(false);
    }
  };

  const confirmDisconnect = async () => {
    if (!deletingId) {return;}
    try {
      await integrationsClient.deleteShopeeCredential(deletingId);
      notify({ tone: "success", title: "Loja Shopee desconectada" });
      setStock(null);
      loadCredentials();
    } catch {
      notify({ tone: "error", title: "Erro ao desconectar" });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <Card>
        <CardHeader
          title="Shopee"
          description="Loja conectada via Shopee Open Platform para leitura de estoque."
          action={
            !connected && !isLoading ? (
              <Button onClick={handleConnect} disabled={connecting} className="gap-2">
                {connecting ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Plus className="h-4 w-4" aria-hidden="true" />
                )}
                Conectar Shopee
              </Button>
            ) : undefined
          }
        />
        <CardBody className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-brand-700" />
            </div>
          ) : credentials.length === 0 ? (
            <EmptyState
              icon={<Store aria-hidden="true" className="h-8 w-8" />}
              title="Nenhuma loja Shopee conectada"
              description="Conecte sua loja para consultar o estoque dos anúncios e compará-lo com o catálogo."
            />
          ) : (
            <div className="divide-y divide-line">
              {credentials.map((credential) => (
                <div
                  key={credential.id}
                  className="flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between"
                >
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-orange-100">
                      <Store aria-hidden="true" className="h-6 w-6 text-orange-600" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-900">{credential.account_name}</h3>
                      <p className="text-sm text-slate-500">ID da loja: {credential.shop_id ?? "—"}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={credential.is_active ? "success" : "warning"}>
                      {credential.is_active ? "Conectado" : "Desconectado"}
                    </Badge>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleSync}
                      disabled={syncing}
                      className="gap-2"
                    >
                      <RefreshCw
                        aria-hidden="true"
                        className={syncing ? "h-4 w-4 animate-spin" : "h-4 w-4"}
                      />
                      Sincronizar
                    </Button>
                    <button
                      type="button"
                      onClick={() => setDeletingId(credential.id)}
                      aria-label={`Desconectar ${credential.account_name}`}
                      className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 aria-hidden="true" className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      {connected && (
        <Card>
          <CardHeader
            title="Estoque na Shopee"
            description={
              stock && !stock.catalog_available
                ? "Catálogo indisponível: exibindo apenas os dados da Shopee."
                : "Diferença positiva = Shopee anuncia mais do que existe no Stock Sync (risco de overselling)."
            }
          />
          <CardBody className="p-0">
            {isLoadingStock && !stock ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-brand-700" />
              </div>
            ) : stock && stock.items.length > 0 ? (
              <>
                <Table
                  caption="Estoque dos anúncios da Shopee"
                  columns={columns}
                  rows={stock.items}
                  rowKey={(row) => `${row.item_id}-${row.model_id ?? 0}`}
                />
                <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-slate-500">
                  <span>{stock.total} anúncios na loja</span>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={offset === 0 || isLoadingStock}
                      onClick={() => loadStock(Math.max(0, offset - PAGE_SIZE))}
                    >
                      Anterior
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!stock.has_next_page || isLoadingStock}
                      onClick={() => loadStock(stock.next_offset ?? offset + PAGE_SIZE)}
                    >
                      Próxima
                    </Button>
                  </div>
                </div>
              </>
            ) : (
              <EmptyState
                icon={<ExternalLink aria-hidden="true" className="h-8 w-8" />}
                title="Nenhum anúncio encontrado"
                description="A loja não possui anúncios ativos ou pausados."
              />
            )}
          </CardBody>
        </Card>
      )}

      <ConfirmDialog
        open={deletingId !== null}
        title="Desconectar Shopee"
        description="A conexão com a loja será removida. Os anúncios continuam ativos na Shopee, mas o estoque deixará de ser consultado."
        confirmLabel="Desconectar"
        cancelLabel="Cancelar"
        onCancel={() => setDeletingId(null)}
        onConfirm={confirmDisconnect}
      />
    </>
  );
}
