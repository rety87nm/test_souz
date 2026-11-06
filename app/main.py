from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.llm import LLMClient, LLMError, LLMTimeoutError
from app.schemas import GenerateResult, ProductInput
from app.service import CardValidationError, generate_card

DATA_DIR = config.BASE_DIR / "data"
TEMPLATES_DIR = config.BASE_DIR / "app" / "templates"
STATIC_DIR = config.BASE_DIR / "app" / "static"


def load_examples() -> list[dict[str, Any]]:
    path = DATA_DIR / "products.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = LLMClient()
    yield
    await app.state.llm.close()


app = FastAPI(
    title="AI-помощник для товарных описаний",
    description="Генерация черновика карточки контрольно-измерительного прибора через LLM",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        details.append({"field": location or "body", "message": error.get("msg", "Ошибка валидации")})
    return JSONResponse(
        status_code=422,
        content={"error": "Некорректные входные данные", "details": details},
    )


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"examples": load_examples()},
    )


@app.get("/api/health")
async def health(request: Request):
    try:
        data = await request.app.state.llm.health()
    except LLMTimeoutError as exc:
        return JSONResponse(status_code=504, content={"status": "error", "error": str(exc)})
    except LLMError as exc:
        return JSONResponse(status_code=503, content={"status": "error", "error": str(exc)})

    models = data.get("data") or data.get("models") or []
    names = [item.get("id") or item.get("name") for item in models if isinstance(item, dict)]
    return {"status": "ok", "model": config.MODEL, "available_models": names}


@app.get("/api/examples")
async def examples() -> list[dict[str, Any]]:
    return load_examples()


@app.post("/api/generate", response_model=GenerateResult)
async def generate(product: ProductInput, request: Request) -> GenerateResult:
    try:
        result = await generate_card(product, request.app.state.llm)
    except CardValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return GenerateResult(**result)
