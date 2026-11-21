"""Server configuration. Secrets live here and in .env, never in web/."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

# Deliberately no load_dotenv() here. Calling it at import time copies every
# line of .env into os.environ, including the blank ones, and a process
# environment variable outranks any env_file passed later. That made an
# explicit Settings(_env_file=...) silently ignore its own file. Let
# pydantic-settings read .env itself, from an absolute path so the result does
# not depend on the current working directory.


class Settings(BaseSettings):
    """Everything the server reads from the environment.

    Defaults are chosen so a fresh clone runs fully mocked and offline. Turning
    a mock off is a deliberate act.
    """

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: object) -> object:
        """An empty value in .env means "not configured", not "empty string".

        .env.example lists every variable with no value, and both the README
        and scripts/dev tell you to copy it to .env. Without this, that copy
        makes ETHYNEON_PORT="" and the whole app refuses to start on a typed
        field. Dropping blanks lets the declared defaults apply.
        """
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and value.strip() == "")
            }
        return data

    # Mock flags
    mock_ai: bool = Field(default=True, alias="MOCK_AI")
    mock_commerce: bool = Field(default=True, alias="MOCK_COMMERCE")
    mock_room_input: bool = Field(default=True, alias="MOCK_ROOM_INPUT")
    mock_agora: bool = Field(default=True, alias="MOCK_AGORA")

    # Gemini and ADK
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")

    # Shopify Storefront
    shopify_store_domain: Optional[str] = Field(default=None, alias="SHOPIFY_STORE_DOMAIN")
    shopify_storefront_token: Optional[str] = Field(default=None, alias="SHOPIFY_STOREFRONT_TOKEN")
    shopify_api_version: str = Field(default="2025-10", alias="SHOPIFY_API_VERSION")

    # Order lookup, separate credential, off by default
    enable_order_lookup: bool = Field(default=False, alias="ENABLE_ORDER_LOOKUP")
    shopify_admin_api_token: Optional[str] = Field(default=None, alias="SHOPIFY_ADMIN_API_TOKEN")

    # Shop MCP, discovery only
    shop_mcp: bool = Field(default=True, alias="SHOP_MCP")
    shop_mcp_endpoint: str = Field(default="https://mcp.shop.com/", alias="SHOP_MCP_ENDPOINT")
    shop_mcp_protocol_version: str = Field(
        default="2026-07-28", alias="SHOP_MCP_PROTOCOL_VERSION"
    )
    shop_mcp_timeout_seconds: float = Field(default=20.0, alias="SHOP_MCP_TIMEOUT_SECONDS")
    shop_mcp_cache_ttl_ms_override: Optional[int] = Field(
        default=None, alias="SHOP_MCP_CACHE_TTL_MS_OVERRIDE"
    )

    # Agora
    agora_app_id: Optional[str] = Field(default=None, alias="AGORA_APP_ID")
    agora_app_certificate: Optional[str] = Field(default=None, alias="AGORA_APP_CERTIFICATE")
    agora_token_ttl_seconds: int = Field(default=3600, alias="AGORA_TOKEN_TTL_SECONDS")

    # Backend
    db_path: str = Field(default=str(REPO_ROOT / "ethyneon.sqlite"), alias="ETHYNEON_DB_PATH")
    host: str = Field(default="127.0.0.1", alias="ETHYNEON_HOST")
    port: int = Field(default=8000, alias="ETHYNEON_PORT")
    log_level: str = Field(default="info", alias="ETHYNEON_LOG_LEVEL")
    cors_origins: str = Field(default="http://localhost:5173", alias="ETHYNEON_CORS_ORIGINS")
    approval_ttl_seconds: int = Field(default=900, alias="APPROVAL_TTL_SECONDS")
    checkout_audit_ttl_seconds: int = Field(default=600, alias="CHECKOUT_AUDIT_TTL_SECONDS")

    # Fit engine
    fit_clearance_margin_m: float = Field(default=0.05, alias="FIT_CLEARANCE_MARGIN_M")
    fit_min_walkway_width_m: float = Field(default=0.75, alias="FIT_MIN_WALKWAY_WIDTH_M")
    fit_door_swing_steps: int = Field(default=7, alias="FIT_DOOR_SWING_STEPS")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def commerce_is_live(self) -> bool:
        """True only when a real store is configured and the mock is off."""
        return (
            not self.mock_commerce
            and bool(self.shopify_store_domain)
            and bool(self.shopify_storefront_token)
        )

    def active_mocks(self) -> list[str]:
        """What is faked right now, for the banner and the README check."""
        flags = {
            "MOCK_AI": self.mock_ai,
            "MOCK_COMMERCE": self.mock_commerce,
            "MOCK_ROOM_INPUT": self.mock_room_input,
            "MOCK_AGORA": self.mock_agora,
        }
        return [name for name, on in flags.items() if on]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Tests change the environment, then call this."""
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache", "REPO_ROOT"]
