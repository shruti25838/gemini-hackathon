from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.connections import INTERACTION_ID, SHOPIFY_PRODUCT_ID, SHOPIFY_VARIANT_ID


GeminiInput = Annotated[str, Field(min_length=1)] | Annotated[list[dict[str, Any]], Field(min_length=1)]
GeminiModel = Literal["gemini-4-hydrogen", "gemini-4-neon", "gemini-4-argon", "gemini-4-carbon"]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeminiInteractionRequest(RequestBody):
    input: GeminiInput
    model: GeminiModel = "gemini-4-hydrogen"
    generation_config: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    previous_interaction_id: str | None = Field(default=None, pattern=INTERACTION_ID)
    background: bool = False


class DecisionQuestion(RequestBody):
    type: Literal["noul", "choice", "score"]
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] | list[str] | None = None


class GeminiDecisionRequest(RequestBody):
    input: GeminiInput
    questions: dict[str, DecisionQuestion] = Field(min_length=1, max_length=1000)


class ManagedRunRequest(RequestBody):
    input: GeminiInput
    model: Literal["gemini-4-argon", "gemini-4-carbon"] = "gemini-4-argon"
    max_total_tokens: int = Field(default=20000, gt=0)
    previous_interaction_id: str | None = Field(default=None, pattern=INTERACTION_ID)


class CatalogContext(RequestBody):
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    def shop_context(self) -> dict[str, str]:
        context = {"address_country": self.country}
        if self.currency:
            context["currency"] = self.currency
        return context


class CatalogImage(RequestBody):
    content_type: Literal["image/jpeg", "image/png", "image/webp", "image/avif", "image/heic"]
    data: str = Field(min_length=1, max_length=4_000_000)


class CatalogSearchRequest(CatalogContext):
    query: str | None = Field(default=None, min_length=1)
    image: CatalogImage | None = None
    limit: int = Field(default=10, ge=1, le=50)
    cursor: str | None = None
    min_price_minor: int | None = Field(default=None, ge=0)
    max_price_minor: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_search(self) -> Self:
        if not (self.query and self.query.strip()) and self.image is None:
            raise ValueError("Provide a product query or image.")
        if (self.min_price_minor is not None or self.max_price_minor is not None) and self.currency is None:
            raise ValueError("Set the currency when filtering on price.")
        if self.min_price_minor is not None and self.max_price_minor is not None:
            if self.min_price_minor > self.max_price_minor:
                raise ValueError("The minimum price cannot exceed the maximum price.")
        return self

    def shop_catalog(self) -> dict[str, Any]:
        pagination = {"limit": self.limit}
        if self.cursor:
            pagination["cursor"] = self.cursor
        catalog: dict[str, Any] = {"context": self.shop_context(), "pagination": pagination}
        if self.query and (query := self.query.strip()):
            catalog["query"] = query
        if self.image:
            catalog["like"] = [{"image": self.image.model_dump()}]
        price = {
            bound: amount
            for bound, amount in (("min", self.min_price_minor), ("max", self.max_price_minor))
            if amount is not None
        }
        if price:
            catalog["filters"] = {"price": price}
        return catalog


class VariantOption(RequestBody):
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)


class CatalogProductRequest(CatalogContext):
    product_id: str = Field(pattern=SHOPIFY_PRODUCT_ID)
    selected: list[VariantOption] = Field(default_factory=list)

    def shop_catalog(self) -> dict[str, Any]:
        catalog: dict[str, Any] = {"id": self.product_id, "context": self.shop_context()}
        if self.selected:
            catalog["selected"] = [option.model_dump() for option in self.selected]
        return catalog


class CatalogVariantRequest(CatalogContext):
    variant_id: str = Field(pattern=SHOPIFY_VARIANT_ID)
