from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "StockSync"
    app_version: str = "0.1.0"
    debug: bool = False

    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/stocksync_integrations"
    )
    db_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"
    mercado_livre_app_id: str = ""
    mercado_livre_client_secret: str = ""
    # Shopee Open Platform (v2) — credenciais do app criado no Open Platform Console
    shopee_partner_id: str = ""
    shopee_partner_key: str = ""
    # Host da API. Produção: https://partner.shopeemobile.com
    # Sandbox: https://partner.test-stable.shopeemobile.com (confira no Console)
    shopee_api_host: str = "https://partner.shopeemobile.com"
    # Host aberto pelo NAVEGADOR na tela de autorização. Vazio = mesmo de shopee_api_host.
    # Só difere com o simulador local (navegador usa localhost, o serviço usa a rede Docker).
    shopee_auth_host: str = ""
    # URL pública que a Shopee chama após a autorização da loja. O domínio precisa
    # estar cadastrado como "Redirect URL Domain" no app do Console.
    shopee_redirect_url: str = "http://localhost:5173/api/v1/auth/shopee/callback"
    # Vitrine usada para montar o link público do anúncio
    shopee_shop_url: str = "https://shopee.com.br"

    # URL do frontend (para redirecionar o usuário após o OAuth)
    frontend_url: str = "http://localhost:5173"
    # catalog-service, usado para cruzar o estoque da Shopee com os SKUs internos
    catalog_service_url: str = "http://localhost:8001"

    # JWT settings (shared with auth-service for token validation)
    jwt_secret_key: str = "your-super-secret-jwt-key-change-in-production"
    jwt_algorithm: str = "HS256"

    # Cloudinary Configuration
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql+psycopg2://"):
            return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
