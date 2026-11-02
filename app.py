from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from rulea import ENGINE_VERSION
from rulea.quality import lint, run_suite, semantic_diff
from rulease import __version__ as RULEASE_VERSION
from rulease.service import preview as rulease_preview

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
POLICY_PATH = BASE_DIR / "policy" / "arcade.rulea"
DEFAULT_SOURCE = POLICY_PATH.read_text(encoding="utf-8")

app = FastAPI(
    title="TANUKI Rulease Control Room",
    version="2.0.0",
    description="Interactive policy lab powered by Rulease v2 and rulea.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ALLOWED_RULES = {
    "arcade.entry": "総合入場ゲート",
    "arcade.vip": "VIP昇格判定",
    "arcade.coupon": "クーポン配布",
    "arcade.midnight": "深夜活動許可",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArcadeContext(StrictModel):
    age: int = Field(ge=0, le=120)
    trust: int = Field(ge=0, le=100)
    chaos: int = Field(ge=0, le=100)
    cart_total: int = Field(ge=0, le=1_000_000)
    loyalty: int = Field(ge=0, le=10)
    risk: int = Field(ge=0, le=100)
    banned: bool
    night_owl: bool
    beta_tester: bool = False


class DecisionRequest(StrictModel):
    rule: str
    context: ArcadeContext


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


class MatrixRequest(StrictModel):
    source: str = Field(min_length=1, max_length=50_000)
    rule: str = Field(min_length=1, max_length=256)
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=30)


def _remote_config() -> dict[str, str] | None:
    url = os.getenv("RULEASE_URL", "").rstrip("/")
    workspace = os.getenv("RULEASE_WORKSPACE", "")
    project = os.getenv("RULEASE_PROJECT", "arcade")
    policy = os.getenv("RULEASE_POLICY", "admission")
    environment = os.getenv("RULEASE_ENVIRONMENT", "production")
    if not (url and workspace):
        return None
    return {
        "url": url,
        "workspace": workspace,
        "project": project,
        "policy": policy,
        "environment": environment,
        "api_key": os.getenv("RULEASE_API_KEY", ""),
        "human_token": os.getenv("RULEASE_HUMAN_TOKEN", ""),
    }


def _safe_preview(source: str, rule: str, context: dict[str, Any], trace: str = "full") -> dict[str, Any]:
    if trace not in {"none", "summary", "full"}:
        raise HTTPException(400, "trace must be none, summary, or full")
    started = time.perf_counter_ns()
    try:
        result = rulease_preview(source, rule, context, trace=trace)
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc
    result["duration_us"] = (time.perf_counter_ns() - started) // 1000
    result["source"] = "embedded-rulease"
    return result


async def _remote_get(path: str, *, human: bool = False) -> tuple[int, Any]:
    cfg = _remote_config()
    if cfg is None:
        return 0, None
    token = cfg["human_token"] if human else cfg["api_key"]
    headers = {"X-Rulease-Workspace": cfg["workspace"]}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(cfg["url"] + path, headers=headers)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body
    except httpx.RequestError:
        return -1, None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "rulease": RULEASE_VERSION, "engine": ENGINE_VERSION}


@app.get("/api/policy")
def policy() -> dict[str, Any]:
    return {
        "source": DEFAULT_SOURCE,
        "rules": ALLOWED_RULES,
        "rulease_version": RULEASE_VERSION,
        "engine_version": ENGINE_VERSION,
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    cfg = _remote_config()
    remote: dict[str, Any] = {
        "configured": bool(cfg),
        "decision_key": bool(cfg and cfg["api_key"]),
        "human_token": bool(cfg and cfg["human_token"]),
        "live": None,
        "ready": None,
    }
    if cfg:
        live_code, live_body = await _remote_get("/health/live")
        ready_code, ready_body = await _remote_get("/health/ready")
        remote.update(
            live=live_code == 200,
            ready=ready_code == 200,
            live_detail=live_body,
            ready_detail=ready_body,
            project=cfg["project"],
            policy=cfg["policy"],
            environment=cfg["environment"],
        )
    return {
        "mode": "remote+embedded" if cfg else "embedded",
        "rulease_version": RULEASE_VERSION,
        "engine_version": ENGINE_VERSION,
        "remote": remote,
        "capabilities": [
            "preview",
            "lint",
            "semantic_diff",
            "test_matrix",
            "trace",
            "remote_decision" if cfg and cfg["api_key"] else "embedded_decision",
        ],
    }


@app.post("/api/decide")
async def decide(body: DecisionRequest) -> dict[str, Any]:
    if body.rule not in ALLOWED_RULES:
        raise HTTPException(400, "Unknown arcade rule")

    cfg = _remote_config()
    if cfg and cfg["api_key"]:
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
                response = await client.post(cfg["url"] + "/v2/decisions", json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(502, f"Remote Rulease unreachable: {exc.__class__.__name__}") from exc
        if response.status_code < 400:
            data = response.json()
            data["source"] = "remote-rulease"
            return data
        raise HTTPException(502, f"Remote Rulease rejected decision ({response.status_code})")

    return _safe_preview(DEFAULT_SOURCE, body.rule, body.context.model_dump(), trace="summary")


@app.post("/api/lab/preview")
def lab_preview(body: PreviewRequest) -> dict[str, Any]:
    return _safe_preview(body.source, body.rule, body.context, body.trace)


@app.post("/api/lab/lint")
def lab_lint(body: LintRequest) -> dict[str, Any]:
    try:
        diagnostics = [item.to_dict() for item in lint(body.source)]
        return {
            "diagnostics": diagnostics,
            "errors": sum(1 for item in diagnostics if item.get("severity") == "ERROR"),
            "warnings": sum(1 for item in diagnostics if item.get("severity") == "WARNING"),
        }
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc


@app.post("/api/lab/diff")
def lab_diff(body: DiffRequest) -> dict[str, Any]:
    try:
        return semantic_diff(body.before, body.after)
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc


@app.post("/api/lab/matrix")
def lab_matrix(body: MatrixRequest) -> dict[str, Any]:
    suite = []
    for i, context in enumerate(body.cases):
        suite.append(
            {
                "name": f"scenario-{i + 1}",
                "rule": body.rule,
                "context": context,
                "expect": {},
            }
        )
    try:
        report = run_suite(body.source, suite)
    except Exception as exc:
        raise HTTPException(422, f"{exc.__class__.__name__}: {exc}") from exc
    rows = []
    for item in report.get("cases", []):
        rows.append(
            {
                "name": item.get("name"),
                "passed": item.get("passed"),
                "actual": item.get("actual"),
                "reason": item.get("reason"),
            }
        )
    return {"total": report.get("total", len(rows)), "rows": rows, "raw": report}


@app.get("/api/control-plane")
async def control_plane() -> dict[str, Any]:
    cfg = _remote_config()
    if cfg is None:
        return {
            "connected": False,
            "message": "Set RULEASE_URL and RULEASE_WORKSPACE to attach a remote Rulease control plane.",
        }

    result: dict[str, Any] = {
        "connected": True,
        "project": cfg["project"],
        "policy": cfg["policy"],
        "environment": cfg["environment"],
        "management_enabled": bool(cfg["human_token"]),
    }
    if not cfg["human_token"]:
        result["message"] = "Remote health is available. Add RULEASE_HUMAN_TOKEN for policy/environment inspection."
        return result

    project = cfg["project"]
    policy_key = cfg["policy"]
    environment = cfg["environment"]
    policies_code, policies_body = await _remote_get(f"/v2/projects/{project}/policies", human=True)
    env_code, env_body = await _remote_get(f"/v2/projects/{project}/environments/{environment}", human=True)
    draft_code, draft_body = await _remote_get(f"/v2/projects/{project}/policies/{policy_key}/draft", human=True)
    result.update(
        policies=policies_body if policies_code == 200 else {"status": policies_code},
        environment_state=env_body if env_code == 200 else {"status": env_code},
        draft=draft_body if draft_code == 200 else {"status": draft_code},
    )
    return result
