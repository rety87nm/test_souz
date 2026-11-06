from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    manufacturer: str | None = None
    specs: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Название товара не может быть пустым")
        return cleaned

    @field_validator("manufacturer", "description")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("specs", mode="before")
    @classmethod
    def normalize_specs(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("Поле specs должно быть объектом вида {\"характеристика\": \"значение\"}")

        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if isinstance(raw_value, (dict, list)):
                raise ValueError(f"Значение характеристики «{key}» должно быть строкой или числом")
            if raw_value is None:
                normalized[key] = ""
            else:
                normalized[key] = str(raw_value).strip()
        return normalized

    @model_validator(mode="after")
    def validate_content(self) -> ProductInput:
        if not self.specs and not (self.description or "").strip():
            raise ValueError("Заполните хотя бы характеристики (specs) или описание (description)")
        return self

    def facts(self) -> dict[str, Any]:
        return self.model_dump()


class SpecItem(BaseModel):
    name: str
    value: str


class ProductCard(BaseModel):
    product_name: str
    manufacturer: str
    seo_title: str
    seo_description: str
    key_specs: list[SpecItem]
    search_queries: list[str]
    commercial_block: str
    missing_fields: list[str]
    warnings: list[str] = Field(default_factory=list)


class GenerateResult(BaseModel):
    card: ProductCard
    markdown: str
    meta: dict[str, Any]
