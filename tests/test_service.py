from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ProductCard, ProductInput, SpecItem
from app.service import (
    build_key_specs,
    card_to_markdown,
    collect_missing_fields,
    _draft_from_response,
    _finalize,
    _grounding_problems,
    _strip_ungrounded,
    _truncate_words,
)


def test_missing_fields_detected() -> None:
    product = ProductInput(
        name="Прибор",
        manufacturer="",
        specs={"диапазон": "", "класс точности": "1,5"},
        description="",
    )
    missing = collect_missing_fields(product)
    assert "производитель" in missing
    assert "описание" in missing
    assert "характеристика «диапазон»" in missing
    assert "характеристика «класс точности»" not in missing


def test_key_specs_use_no_data_marker() -> None:
    product = ProductInput(name="Прибор", specs={"диапазон": "", "класс": "1,5"})
    specs = build_key_specs(product)
    assert specs[0] == SpecItem(name="диапазон", value="данных нет")
    assert specs[1] == SpecItem(name="класс", value="1,5")


def test_key_specs_when_specs_empty() -> None:
    product = ProductInput(name="Прибор", description="Описание без характеристик")
    specs = build_key_specs(product)
    assert specs == [SpecItem(name="Технические характеристики", value="данных нет")]


def test_product_requires_specs_or_description() -> None:
    with pytest.raises(ValidationError):
        ProductInput(name="Прибор", specs={}, description="   ")


def test_invalid_specs_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ProductInput(name="Прибор", specs={"узел": {"вложенный": "объект"}})


def test_draft_detects_length_problems() -> None:
    card, problems = _draft_from_response(
        {
            "seo_title": "Очень длинный заголовок " * 5,
            "seo_description": "Коротко.",
            "search_queries": ["один"],
            "commercial_block": "Подходит для задач.",
            "missing_fields": [],
        }
    )
    assert card.seo_title
    assert any("заголовок" in problem for problem in problems)
    assert any("описание" in problem for problem in problems)
    assert any("запрос" in problem for problem in problems)


def test_truncate_words() -> None:
    assert len(_truncate_words("один два три четыре", 12)) <= 12
    assert _truncate_words("короткий", 20) == "короткий"


def test_markdown_contains_required_blocks() -> None:
    card = ProductCard(
        product_name="Прибор",
        manufacturer="Завод",
        seo_title="Прибор для измерений",
        seo_description="Описание",
        key_specs=[SpecItem(name="диапазон", value="0–1 МПа")],
        search_queries=["прибор купить"],
        commercial_block="Для менеджера.",
        missing_fields=["описание"],
    )
    markdown = card_to_markdown(card)
    assert "# Прибор для измерений" in markdown
    assert "| диапазон | 0–1 МПа |" in markdown
    assert "## Недостающие или требующие уточнения данные" in markdown


def _card(**overrides) -> ProductCard:
    data = {
        "product_name": "Прибор",
        "manufacturer": "Завод",
        "seo_title": "Прибор для измерений",
        "seo_description": "Описание прибора.",
        "key_specs": [],
        "search_queries": ["прибор купить", "прибор цена", "прибор характеристики"],
        "commercial_block": "Подходит для задач.",
        "missing_fields": [],
    }
    data.update(overrides)
    return ProductCard(**data)


def test_grounding_catches_invented_power_and_numbers() -> None:
    product = ProductInput(name="Прибор", specs={"диапазон": "0–1 МПа"})
    card = _card(seo_description="Прибор работает без внешнего питания при 25 °C. Facilitate монтаж.")
    problems = _grounding_problems(card, product)
    assert any("питание" in problem for problem in problems)
    assert any("25" in problem for problem in problems)
    assert any("facilitate" in problem.casefold() for problem in problems)


def test_strip_ungrounded_removes_bad_sentences() -> None:
    product = ProductInput(name="Прибор", specs={"диапазон": "0–1 МПа"})
    text = "Прибор измеряет давление. Прибор работает без питания. Диапазон 0–1 МПа."
    cleaned = _strip_ungrounded(text, product)
    assert "питания" not in cleaned
    assert "измеряет давление" in cleaned


def test_finalize_removes_missing_field_mentions() -> None:
    product = ProductInput(
        name="Прибор",
        specs={"диапазон": "0–1 МПа", "материал корпуса": ""},
        description="Описание",
    )
    card = _card(
        seo_title="Прибор",
        seo_description="Прибор измеряет давление. Материал корпуса не указан.",
        commercial_block="Подходит для задач.",
    )
    final = _finalize(card, product, 1)
    assert "Материал корпуса" not in final.seo_description
    assert "характеристика «материал корпуса»" in final.missing_fields
