from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from rulea import ENGINE_VERSION
from rulea.quality import lint, semantic_diff
from rulease import __version__ as RULEASE_VERSION
from rulease.service import preview as rulease_preview

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
POLICY_PATH = BASE_DIR / "policy" / "arcade.rulea"
DEFAULT_SOURCE = POLICY_PATH.read_text(encoding="utf-8")

app = FastAPI(title="TANUKI BORDER PROTOCOL", version="3.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

RULES = {
    "border.entry": "入国可否",
    "border.detain": "拘束",
    "border.deport": "強制退去",
    "border.surveillance": "監視指定",
    "border.vip": "外交VIP",
    "border.citizenship_offer": "市民権オファー",
    "border.secret_tunnel": "極秘通路",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Applicant(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    age: int = Field(ge=0, le=120)
    cash: int = Field(ge=0, le=10_000_000)
    visits: int = Field(ge=0, le=99)
    trust: int = Field(ge=0, le=100)
    risk: int = Field(ge=0, le=100)
    contraband: bool
    wanted: bool
    forged_docs: bool
    vip_invite: bool
    government_contact: bool
    night_arrival: bool
    bomb_joke: bool
    declared_truthfully: bool
    pineapple_pizza: bool
    machine_flattery: bool


class InspectRequest(StrictModel):
    applicant: Applicant
    source: str | None = Field(default=None, max_length=50_000)


class PreviewRequest(StrictModel):
    source: str = Field(min_length=1, max_length=50_000)
    rule: str = Field(min_length=1, max_length=256)
    context: dict[str, Any]
    trace: str = "full"


class LintRequest(StrictModel):
    source: str = Field(min_length=1, max_length=50_000)


class DiffRequest(StrictModel):
    before: str = Field(min_length=1, max_length=50_000)
    after: str = Field(min_length=1, max_length=50_000)


def _context(applicant: Applicant) -> dict[str, Any]:
    data = applicant.model_dump()
    data.pop("name", None)
    return data


def _preview(source: str, rule: str, context: dict[str, Any], trace: str = "full") -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        result = rulease_preview(source, rule, context, trace=trace)
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc
    result["duration_us"] = (time.perf_counter_ns() - started) // 1000
    return result


def _allowed(result: dict[str, Any]) -> bool:
    return str(result.get("result", "")).upper() == "ALLOW"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "rulease": RULEASE_VERSION, "engine": ENGINE_VERSION}


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return {
        "source": DEFAULT_SOURCE,
        "rulease_version": RULEASE_VERSION,
        "engine_version": ENGINE_VERSION,
        "rules": RULES,
    }


@app.post("/api/inspect")
def inspect(body: InspectRequest) -> dict[str, Any]:
    source = body.source or DEFAULT_SOURCE
    context = _context(body.applicant)
    decisions = {rule: _preview(source, rule, context, trace="summary") for rule in RULES}

    if _allowed(decisions["border.detain"]):
        outcome = "DETAIN"
        headline = "別室へ。質問はあとです。"
        stamp = "拘束"
    elif _allowed(decisions["border.deport"]):
        outcome = "DEPORT"
        headline = "入国は拒否されました。帰りの便は国家負担ではありません。"
        stamp = "退去"
    elif _allowed(decisions["border.secret_tunnel"]):
        outcome = "SECRET"
        headline = "床のタイルが一枚だけ沈んだ。係官は何も見ていない。"
        stamp = "存在しない"
    elif _allowed(decisions["border.citizenship_offer"]):
        outcome = "CITIZEN"
        headline = "おめでとうございます。今日から税金を払えます。"
        stamp = "市民権"
    elif _allowed(decisions["border.vip"]):
        outcome = "VIP"
        headline = "通常ゲートを閉鎖。外交ラウンジへ案内します。"
        stamp = "VIP"
    elif _allowed(decisions["border.entry"]):
        outcome = "ALLOW"
        headline = "入国を許可します。国家はあなたを覚えています。"
        stamp = "許可"
    else:
        outcome = "DENY"
        headline = "条件不足。国境線のこちら側でお帰りください。"
        stamp = "拒否"

    flags = []
    for key in ["border.surveillance", "border.vip", "border.citizenship_offer", "border.secret_tunnel"]:
        if _allowed(decisions[key]):
            flags.append(key.split(".", 1)[1])

    return {
        "applicant": body.applicant.name,
        "outcome": outcome,
        "headline": headline,
        "stamp": stamp,
        "flags": flags,
        "decisions": decisions,
        "rulease_version": RULEASE_VERSION,
        "engine_version": ENGINE_VERSION,
        "policy_mode": "edited" if body.source else "released-default",
    }


@app.post("/api/lab/preview")
def lab_preview(body: PreviewRequest) -> dict[str, Any]:
    return _preview(body.source, body.rule, body.context, body.trace)


@app.post("/api/lab/lint")
def lab_lint(body: LintRequest) -> dict[str, Any]:
    try:
        diagnostics = [item.to_dict() for item in lint(body.source)]
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc
    return {"diagnostics": diagnostics}


@app.post("/api/lab/diff")
def lab_diff(body: DiffRequest) -> dict[str, Any]:
    try:
        return semantic_diff(body.before, body.after)
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc
