from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm import LLMClient
from app.schemas import ProductInput
from app.service import generate_card


async def main() -> None:
    products = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))
    output_dir = ROOT / "examples"
    output_dir.mkdir(exist_ok=True)
    llm = LLMClient()

    try:
        for index, product_data in enumerate(products, start=1):
            product = ProductInput.model_validate(product_data)
            result = await generate_card(product, llm)
            stem = f"product_{index}"
            (output_dir / f"{stem}.json").write_text(
                json.dumps(result["card"].model_dump(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (output_dir / f"{stem}.md").write_text(result["markdown"], encoding="utf-8")
            print(
                f"{stem}: {product.name}; "
                f"заголовок {len(result['card'].seo_title)} символов, "
                f"описание {len(result['card'].seo_description)} символов, "
                f"попыток {result['meta']['attempts']}"
            )
    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
