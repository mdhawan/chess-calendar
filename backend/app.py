from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db, latest_refresh, query_tournaments, save_subscription
from .models import Subscription
from .refresh import refresh_all

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

app = FastAPI(title="Chess Calendar (India)")

# Compress the (still sizeable) tournaments JSON on the wire — typically 5–8×.
app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Background refresh state -------------------------------------------------
# The chess-results.com crawl takes minutes; run it off the request thread so
# the "Refresh sources" button returns immediately and the UI can poll status.
_refresh_lock = threading.Lock()
_refresh_state: dict = {"running": False, "started_at": None, "summary": None}


def _run_refresh_bg() -> None:
    try:
        _refresh_state["summary"] = refresh_all()
    finally:
        _refresh_state["running"] = False


def _start_refresh() -> bool:
    """Kick off a background refresh; return False if one is already running."""
    with _refresh_lock:
        if _refresh_state["running"]:
            return False
        _refresh_state["running"] = True
        _refresh_state["started_at"] = datetime.now(timezone.utc).isoformat()
    threading.Thread(target=_run_refresh_bg, daemon=True).start()
    return True


@app.on_event("startup")
def startup() -> None:
    init_db()
    sched = BackgroundScheduler()
    sched.add_job(refresh_all, "interval", hours=12, id="refresh")
    sched.start()
    app.state.scheduler = sched


@app.get("/api/tournaments")
def get_tournaments(
    start: Optional[str] = None,
    end: Optional[str] = None,
    state: Optional[str] = None,
    states: Optional[str] = None,
    upcoming_only: bool = True,
    fide_only: bool = False,
    format: Optional[str] = None,
    source: Optional[str] = None,
):
    state_list = [s.strip() for s in states.split(",") if s.strip()] if states else None
    rows = query_tournaments(
        start=start,
        end=end,
        state=state,
        states=state_list,
        upcoming_only=upcoming_only,
        fide_only=fide_only,
        format=format,
        source=source,
    )
    return {"count": len(rows), "tournaments": rows, "last_refresh": latest_refresh()}


@app.post("/api/refresh")
def manual_refresh():
    """Start a background refresh and return immediately (non-blocking)."""
    started = _start_refresh()
    return {"started": started, "running": True}


@app.get("/api/refresh/status")
def refresh_status():
    return {
        "running": _refresh_state["running"],
        "started_at": _refresh_state["started_at"],
        "last_summary": _refresh_state["summary"],
        "last_refresh": latest_refresh(),
    }


@app.post("/api/subscribe")
def subscribe(sub: Subscription):
    """Phase-2 stub: accept and store W3C PushSubscription objects.

    The dispatch side is intentionally not wired — adding it requires only:
      pip install pywebpush
      from pywebpush import webpush; webpush(sub, data, vapid_*)
    """
    save_subscription(sub.endpoint, sub.keys, sub.states_filter, sub.fide_only)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


def _asset_version() -> str:
    """Max mtime of frontend assets — busts cache whenever they change."""
    if not FRONTEND_DIST.exists():
        return "0"
    candidates = ["index.html", "app.js", "styles.css"]
    mtimes = []
    for name in candidates:
        p = FRONTEND_DIST / name
        if p.exists():
            mtimes.append(int(p.stat().st_mtime))
    return str(max(mtimes)) if mtimes else "0"


@app.middleware("http")
async def cache_control(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        return response
    # index.html must never be cached so the version query string updates;
    # versioned assets can be cached aggressively.
    if path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/", response_class=HTMLResponse)
def index():
    if not FRONTEND_DIST.exists():
        return HTMLResponse("frontend/dist not found", status_code=500)
    html = (FRONTEND_DIST / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__VERSION__", _asset_version()))


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=False), name="frontend")
