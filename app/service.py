from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from app import config
from app.llm import LLMClient
from app.prompt import SYSTEM_PROMPT, build_user_prompt
from app.schemas import ProductCard, ProductInput, SpecItem

MAX_GENERATION_ATTEMPTS = 3
MIN_DESCRIPTION = 800
MAX_DESCRIPTION = 1200
MAX_TITLE = 70
MIN_QUERIES = 3
MAX_QUERIES = 5

UNGROUNDED_TERMS = {
    "питани": "питание",
    "автономн": "автономность",
    "батаре": "батарея",
    "аккумулятор": "аккумулятор",
    "материал": "материал",
    "нержаве": "нержавеющая сталь",
    "латун": "латунь",
    "сталь": "сталь",
    "пластик": "пластик",
    "защит": "степень защиты",
    "интерфейс": "интерфейс",
    "rs-485": "RS-485",
    "rs485": "RS-485",
    "modbus": "Modbus",
    "4-20": "выход 4–20 мА",
    "токов": "токовый выход",
    "реле": "реле",
    "сертифик": "сертификат",
    "гаранти": "гарантия",
    "поверк": "поверка",
    "страна": "страна происхождения",
    "электрон": "электроника",
    "герметичн": "герметичность",
    "теплообмен": "теплообмен",
    "теплов": "тепловой режим",
    "износостойк": "износостойкость",
    "долговечн": "долговечность",
    "надежн": "надёжность",
    "стабильн": "стабильность",
    "погрешност": "погрешность",
    "энерг": "энергопотребление",
    "обслуживан": "обслуживание",
    "конструкц": "конструкция",
    "классическ": "классическое исполнение",
    "простот": "простота",
}


class CardValidationError(ValueError):
    pass


def collect_missing_fields(product: ProductInput) -> list[str]:
    missing: list[str] = []
    if not (product.manufacturer or "").strip():
        missing.append("производитель")
    if not (product.description or "").strip():
        missing.append("описание")
    if not product.specs:
        missing.append("технические характеристики")
    for key, value in product.specs.items():
        if not value.strip():
            missing.append(f"характеристика «{key}»")
    return missing


def build_key_specs(product: ProductInput) -> list[SpecItem]:
    if not product.specs:
        return [SpecItem(name="Технические характеристики", value="данных нет")]
    return [
        SpecItem(name=key, value=value if value.strip() else "данных нет")
        for key, value in product.specs.items()
    ]


def _clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CardValidationError(f"Поле {field} должно быть строкой")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise CardValidationError(f"Поле {field} пустое")
    return cleaned


def _clean_multiline(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CardValidationError(f"Поле {field} должно быть строкой")
    cleaned = value.replace("\r\n", "\n").strip()
    if not cleaned:
        raise CardValidationError(f"Поле {field} пустое")
    return cleaned


def _clean_queries(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise CardValidationError("Поле search_queries должно быть массивом строк")
    queries: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = re.sub(r"\s+", " ", item).strip(" .,;")
        if cleaned and cleaned.casefold() not in {q.casefold() for q in queries}:
            queries.append(cleaned)
    if not queries:
        raise CardValidationError("Поле search_queries пустое")
    return queries


def _clean_missing(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = re.sub(r"\s+", " ", item).strip(" .,;")
        if cleaned and cleaned.casefold() not in {m.casefold() for m in result}:
            result.append(cleaned)
    return result


def _truncate_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return cut or text[:limit].rstrip()


def _truncate_sentences(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    positions = [m.end() for m in re.finditer(r"[.!?](?:\s|$)", cut)]
    if positions:
        return cut[: positions[-1]].strip()
    return _truncate_words(cut, limit)


def _numbers(text: str) -> set[str]:
    tokens = re.findall(r"\d+(?:[.,]\d+)?", text)
    return {(token.replace(",", ".").lstrip("0") or "0") for token in tokens}


def _norm(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _ungrounded_terms(text: str, input_text: str) -> list[str]:
    lowered = _norm(text)
    normalized_input = _norm(input_text)
    return [
        label
        for term, label in UNGROUNDED_TERMS.items()
        if term in lowered and term not in normalized_input
    ]


def _ungrounded_numbers(text: str, input_numbers: set[str]) -> list[str]:
    return sorted(number for number in _numbers(text) if number not in input_numbers)


def _ungrounded_latin_words(text: str, input_text: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", text)
    return list(dict.fromkeys(word for word in words if word.casefold() not in input_text))


def _missing_mentions(text: str, product: ProductInput) -> list[str]:
    lowered = _norm(text)
    found: list[str] = []
    if not (product.manufacturer or "").strip() and "производител" in lowered:
        found.append("производитель")
    for key, value in product.specs.items():
        if not value.strip() and _norm(key) in lowered:
            found.append(key)
    return found


def _grounding_problems(card: ProductCard, product: ProductInput) -> list[str]:
    input_text = json.dumps(product.facts(), ensure_ascii=False).casefold()
    input_numbers = _numbers(input_text)
    problems: list[str] = []

    texts = {
        "SEO-заголовок": card.seo_title,
        "SEO-описание": card.seo_description,
        "Коммерческий блок": card.commercial_block,
    }
    for label, text in texts.items():
        for term in _ungrounded_terms(text, input_text):
            problems.append(f"{label}: упоминание «{term}» отсутствует во входных данных")
        for number in _ungrounded_numbers(text, input_numbers):
            problems.append(f"{label}: число {number} отсутствует во входных данных")
        for word in _ungrounded_latin_words(text, input_text):
            problems.append(f"{label}: латинское слово «{word}» отсутствует во входных данных")
        for field in _missing_mentions(text, product):
            problems.append(f"{label}: упоминается отсутствующее поле «{field}»")

    for index, query in enumerate(card.search_queries, start=1):
        for term in _ungrounded_terms(query, input_text):
            problems.append(f"Поисковый запрос {index}: упоминание «{term}» отсутствует во входных данных")
        for number in _ungrounded_numbers(query, input_numbers):
            problems.append(f"Поисковый запрос {index}: число {number} отсутствует во входных данных")
        for word in _ungrounded_latin_words(query, input_text):
            problems.append(f"Поисковый запрос {index}: латинское слово «{word}» отсутствует во входных данных")

    return list(dict.fromkeys(problems))


def _strip_ungrounded(text: str, product: ProductInput) -> str:
    input_text = json.dumps(product.facts(), ensure_ascii=False).casefold()
    input_numbers = _numbers(input_text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [
        sentence
        for sentence in sentences
        if not _ungrounded_terms(sentence, input_text)
        and not _ungrounded_numbers(sentence, input_numbers)
        and not _ungrounded_latin_words(sentence, input_text)
        and not _missing_mentions(sentence, product)
    ]
    return " ".join(kept).strip()


def _fallback_title(product: ProductInput) -> str:
    parts = [product.name]
    for key, value in product.specs.items():
        if value.strip():
            parts.append(f"{key} {value}")
            break
    return _truncate_words(", ".join(parts), MAX_TITLE)


def _description_addendum(product: ProductInput) -> str:
    parts = [f"{product.name}."]
    if (product.manufacturer or "").strip():
        parts.append(f"Производитель: {product.manufacturer.strip()}.")
    specs = [f"{key} — {value}" for key, value in product.specs.items() if value.strip()]
    if specs:
        parts.append("В предоставленных технических данных указаны: " + "; ".join(specs) + ".")
    if (product.description or "").strip():
        parts.append(product.description.strip())
    return " ".join(parts)


def _pad_description(description: str, product: ProductInput) -> str:
    combined = f"{description} {_description_addendum(product)}".strip()
    if len(combined) < MIN_DESCRIPTION:
        combined = (
            f"{combined} Модель {product.name} предназначена для применения "
            "в составе промышленного оборудования."
        ).strip()
    return combined


def _draft_from_response(raw: dict[str, Any]) -> tuple[ProductCard, list[str]]:
    title = _clean_text(raw.get("seo_title"), "seo_title")
    description = _clean_multiline(raw.get("seo_description"), "seo_description")
    queries = _clean_queries(raw.get("search_queries"))
    commercial = _clean_multiline(raw.get("commercial_block"), "commercial_block")
    missing = _clean_missing(raw.get("missing_fields"))

    problems: list[str] = []
    if len(title) > MAX_TITLE:
        problems.append(f"SEO-заголовок длиннее {MAX_TITLE} символов (сейчас {len(title)})")
    if not MIN_DESCRIPTION <= len(description) <= MAX_DESCRIPTION:
        problems.append(
            f"SEO-описание должно быть {MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов "
            f"(сейчас {len(description)})"
        )
    if not MIN_QUERIES <= len(queries) <= MAX_QUERIES:
        problems.append(f"Нужно от {MIN_QUERIES} до {MAX_QUERIES} поисковых запросов (сейчас {len(queries)})")

    card = ProductCard(
        product_name="",
        manufacturer="",
        seo_title=title,
        seo_description=description,
        key_specs=[],
        search_queries=queries,
        commercial_block=commercial,
        missing_fields=missing,
        warnings=[],
    )
    return card, problems


def _filter_missing(model_missing: list[str], product: ProductInput) -> list[str]:
    present_specs = {key.casefold() for key, value in product.specs.items() if value.strip()}
    result: list[str] = []
    for item in model_missing:
        lowered = item.casefold()
        if any(key in lowered for key in present_specs):
            continue
        if "производител" in lowered and (product.manufacturer or "").strip():
            continue
        if "описан" in lowered and (product.description or "").strip():
            continue
        result.append(item)
    return result


def _merge_missing(deterministic: list[str], from_model: list[str]) -> list[str]:
    merged = list(deterministic)
    seen = {item.casefold() for item in merged}
    for item in from_model:
        lowered = item.casefold()
        if lowered in seen:
            continue
        if any(lowered in existing or existing in lowered for existing in seen):
            continue
        merged.append(item)
        seen.add(lowered)
    return merged


def _finalize(card: ProductCard, product: ProductInput, used_attempts: int) -> ProductCard:
    warnings = list(card.warnings)
    input_text = json.dumps(product.facts(), ensure_ascii=False).casefold()
    input_numbers = _numbers(input_text)

    title = card.seo_title
    if (
        _ungrounded_terms(title, input_text)
        or _ungrounded_numbers(title, input_numbers)
        or _ungrounded_latin_words(title, input_text)
        or _missing_mentions(title, product)
    ):
        title = _fallback_title(product)
        warnings.append("SEO-заголовок заменён на безопасный вариант без неподтверждённых данных")
    if len(title) > MAX_TITLE:
        title = _truncate_words(title, MAX_TITLE)
        warnings.append(f"SEO-заголовок сокращён до {len(title)} символов")

    description = _strip_ungrounded(card.seo_description, product)
    if description != card.seo_description:
        warnings.append("Из SEO-описания удалены фразы с данными, которых нет во входных данных")
    if len(description) > MAX_DESCRIPTION:
        description = _truncate_sentences(description, MAX_DESCRIPTION)
        warnings.append(f"SEO-описание сокращено до {len(description)} символов")
    if len(description) < MIN_DESCRIPTION:
        description = _pad_description(description, product)
        warnings.append("SEO-описание дополнено сведениями из входных данных до требуемого объёма")
    if len(description) > MAX_DESCRIPTION:
        description = _truncate_sentences(description, MAX_DESCRIPTION)
        warnings.append(f"SEO-описание сокращено до {len(description)} символов")
    if len(description) < MIN_DESCRIPTION:
        warnings.append(
            f"SEO-описание короче требуемых {MIN_DESCRIPTION} символов: {len(description)}"
        )

    queries = [
        query
        for query in card.search_queries
        if not _ungrounded_terms(query, input_text)
        and not _ungrounded_numbers(query, input_numbers)
        and not _ungrounded_latin_words(query, input_text)
    ]
    if len(queries) != len(card.search_queries):
        warnings.append("Из поисковых запросов удалены варианты с неподтверждёнными данными")
    queries = queries[:MAX_QUERIES]
    if len(queries) < MIN_QUERIES:
        warnings.append(f"Поисковых запросов меньше {MIN_QUERIES}: {len(queries)}")

    commercial = _strip_ungrounded(card.commercial_block, product)
    if not commercial:
        commercial = (
            f"{product.name}: данные для коммерческого описания ограничены "
            "предоставленными характеристиками."
        )
        warnings.append("Коммерческий блок заменён на безопасную формулировку")
    elif commercial != card.commercial_block:
        warnings.append("Из коммерческого блока удалены фразы с неподтверждёнными данными")

    deterministic_missing = collect_missing_fields(product)
    model_missing = _filter_missing(card.missing_fields, product)
    missing = _merge_missing(deterministic_missing, model_missing)

    if used_attempts > 1:
        warnings.append(f"Потребовалось попыток генерации: {used_attempts}")

    return ProductCard(
        product_name=product.name,
        manufacturer=product.manufacturer or "данных нет",
        seo_title=title,
        seo_description=description,
        key_specs=build_key_specs(product),
        search_queries=queries,
        commercial_block=commercial,
        missing_fields=missing,
        warnings=warnings,
    )


def card_to_markdown(card: ProductCard) -> str:
    lines = [
        f"# {card.seo_title}",
        "",
        f"**Производитель:** {card.manufacturer}",
        "",
        "## SEO-описание",
        "",
        card.seo_description,
        "",
        "## Ключевые характеристики",
        "",
        "| Характеристика | Значение |",
        "| --- | --- |",
    ]
    for item in card.key_specs:
        lines.append(f"| {item.name} | {item.value} |")

    lines.extend(["", "## Поисковые запросы", ""])
    lines.extend(f"- {query}" for query in card.search_queries)

    lines.extend(["", "## Коммерческий блок", "", card.commercial_block, ""])

    if card.missing_fields:
        lines.extend(["## Недостающие или требующие уточнения данные", ""])
        lines.extend(f"- {field}" for field in card.missing_fields)
        lines.append("")

    if card.warnings:
        lines.extend(["## Предупреждения", ""])
        lines.extend(f"- {warning}" for warning in card.warnings)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


async def generate_card(product: ProductInput, llm: LLMClient) -> dict[str, Any]:
    started = time.perf_counter()
    deterministic_missing = collect_missing_fields(product)
    product_facts = product.facts()
    last_card: ProductCard | None = None
    feedback: str | None = None
    used_attempts = 0
    raw_response: dict[str, Any] | None = None

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        used_attempts = attempt
        raw_response = await llm.chat_json(
            SYSTEM_PROMPT,
            build_user_prompt(product_facts, deterministic_missing, feedback),
        )
        try:
            card, problems = _draft_from_response(raw_response)
        except CardValidationError as exc:
            feedback = str(exc)
            continue

        problems = list(dict.fromkeys(problems + _grounding_problems(card, product)))
        last_card = card
        if not problems:
            break
        feedback = "; ".join(problems)

    if last_card is None:
        raise CardValidationError(
            "LLM не смогла вернуть корректную структуру карточки. " + (feedback or "")
        )

    final_card = _finalize(last_card, product, used_attempts)
    duration_ms = int((time.perf_counter() - started) * 1000)
    meta = {
        "model": config.MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": used_attempts,
        "duration_ms": duration_ms,
        "missing_input_fields": deterministic_missing,
    }
    return {
        "card": final_card,
        "markdown": card_to_markdown(final_card),
        "meta": meta,
    }
