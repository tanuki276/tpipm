from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Tanuki Policy Arcade", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ALLOWED_RULES = {
    "arcade.vip_gate": "VIPゲート",
    "arcade.coupon_drop": "幻のクーポン",
    "arcade.midnight_license": "深夜活動許可証",
}


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int = Field(ge=0, le=120)
    trust: int = Field(ge=0, le=100)
    chaos: int = Field(ge=0, le=100)
    cart_total: int = Field(ge=0, le=1_000_000)
    loyalty: int = Field(ge=0, le=10)
    risk: int = Field(ge=0, le=100)
    banned: bool
    night_owl: bool


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    context: Context


def _rulease_config() -> dict[str, str] | None:
    url = os.getenv("RULEASE_URL", "").rstrip("/")
    api_key = os.getenv("RULEASE_API_KEY", "")
    workspace = os.getenv("RULEASE_WORKSPACE", "")
    if not (url and api_key and workspace):
        return None
    return {
        "url": url,
        "api_key": api_key,
        "workspace": workspace,
        "project": os.getenv("RULEASE_PROJECT", "arcade"),
        "policy": os.getenv("RULEASE_POLICY", "admission"),
        "environment": os.getenv("RULEASE_ENVIRONMENT", "development"),
    }


def _demo_decision(rule: str, context: Context) -> dict[str, Any]:
    c = context.model_dump()
    checks = {
        "arcade.vip_gate": (
            c["age"] >= 20 and c["trust"] >= 70 and not c["banned"],
            "20歳以上・信頼度70以上・BANされていない者のみVIPへ",
        ),
        "arcade.coupon_drop": (
            c["cart_total"] >= 5000 and c["loyalty"] >= 3 and c["risk"] < 60,
            "5,000円以上・常連度3以上・リスク60未満なら幻のクーポン解禁",
        ),
        "arcade.midnight_license": (
            c["age"] >= 18 and c["night_owl"] and c["chaos"] <= 80 and not c["banned"],
            "18歳以上・夜型・混沌度80以下・BANなしなら深夜活動を許可",
        ),
    }
    allowed, reason = checks[rule]
    return {
        "result": "ALLOW" if allowed else "DENY",
        "rule": rule,
        "reason": reason,
        "decision_id": f"demo_{int(time.time() * 1000)}",
        "revision": 0,
        "duration_us": 0,
        "evaluated_at": "demo",
        "trace": [],
        "decisions": [],
        "source": "demo",
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    cfg = _rulease_config()
    return {
        "mode": "rulease" if cfg else "demo",
        "engine": "Rulease v2 decision API" if cfg else "Local demo evaluator",
        "rules": ALLOWED_RULES,
    }


@app.post("/api/decide")
async def decide(body: DecisionRequest) -> dict[str, Any]:
    if body.rule not in ALLOWED_RULES:
        raise HTTPException(status_code=400, detail="Unknown arcade rule")

    cfg = _rulease_config()
    if cfg is None:
        return _demo_decision(body.rule, body.context)

    payload = {
        "project": cfg["project"],
        "policy": cfg["policy"],
        "environment": cfg["environment"],
        "rule": body.rule,
        "context": body.context.model_dump(),
        "trace": "summary",
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "X-Rulease-Workspace": cfg["workspace"],
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(f"{cfg['url']}/v2/decisions", json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Rulease is unreachable: {exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("title", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=502, detail=f"Rulease rejected the decision: {detail}")

    data = response.json()
    data["source"] = "rulease"
    return data


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
