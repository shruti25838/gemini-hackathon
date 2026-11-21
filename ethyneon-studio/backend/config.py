from dataclasses import dataclass, field
from os import environ
from pathlib import Path
import re

from dotenv import dotenv_values


SHOPIFY_STORE_DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")


@dataclass(frozen=True)
class Settings:
    connection_token: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)
    agora_app_id: str = ""
    agora_app_certificate: str = field(default="", repr=False)
    shopify_shops: tuple[str, ...] = ()
    shopify_storefront_domain: str = ""
    shopify_storefront_token: str = field(default="", repr=False)

    @classmethod
    def load(cls) -> "Settings":
        dotenv_path = Path(__file__).resolve().parents[1] / ".env"
        values = {**dotenv_values(dotenv_path), **environ}

        def value(name: str) -> str:
            return (values.get(name) or "").strip()

        shops = tuple(
            shop.strip().lower()
            for shop in value("SHOPIFY_SHOPS").split(",")
            if shop.strip()
        )
        if any(not SHOPIFY_STORE_DOMAIN.fullmatch(shop) for shop in shops):
            raise ValueError("SHOPIFY_SHOPS must contain canonical myshopify.com domains.")

        storefront = value("SHOPIFY_STOREFRONT_DOMAIN").lower()
        if storefront and not SHOPIFY_STORE_DOMAIN.fullmatch(storefront):
            raise ValueError("SHOPIFY_STOREFRONT_DOMAIN must be a canonical myshopify.com domain.")
        if storefront and shops and storefront not in shops:
            raise ValueError("The Storefront domain must be one of the configured SHOPIFY_SHOPS.")

        return cls(
            connection_token=value("ETHYNEON_CONNECTION_TOKEN"),
            gemini_api_key=value("GEMINI_API_KEY") or value("GOOGLE_API_KEY"),
            agora_app_id=value("AGORA_APP_ID"),
            agora_app_certificate=value("AGORA_APP_CERTIFICATE"),
            shopify_shops=shops,
            shopify_storefront_domain=storefront,
            shopify_storefront_token=value("SHOPIFY_STOREFRONT_TOKEN"),
        )
