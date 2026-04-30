from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from nicegui import app, events, ui

from app.core.engine import JobMatchEngine
from app.core.types import DiscoveredSourceCandidate, FilterCriteria, JobSourceConfig, MatchResult, ScanSummary
from app.utils.config import (
    APP_NAME,
    DEFAULT_SOURCE_MAX_PAGES,
    DEFAULT_SOURCE_REQUEST_DELAY_MS,
    JOB_TYPES,
    REMOTE_MODES,
    SOURCE_TYPES,
    THEME_MODES,
    UPLOADS_DIR,
)
from app.utils.logging import configure_logging
from app.utils.skills import CLEARANCE_PATTERNS, extract_salary_info
from app.utils.text import clipped_excerpt, normalize_whitespace, safe_filename

configure_logging()

if not getattr(app.state, "jobmatch_cors_enabled", False):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.jobmatch_cors_enabled = True

app.colors(
    primary="#0f766e",
    secondary="#be123c",
    accent="#7c3aed",
    positive="#0f766e",
    negative="#dc2626",
    warning="#d97706",
    dark="#0f172a",
)
ui.add_head_html(
    """
    <script>
      window.True = true;
      window.False = false;
      window.None = null;
    </script>
    """,
    shared=True,
)
ui.add_head_html(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet">
    """,
    shared=True,
)
ui.add_css(
    """
    body, .q-layout, .q-page-container, .q-drawer, .q-header { font-family: 'IBM Plex Sans', sans-serif; }
    code, .mono, .score-pill { font-family: 'IBM Plex Mono', monospace; }
    :root {
      --app-bg: #f4f7fb;
      --app-surface: #ffffff;
      --app-surface-2: #e7edf5;
      --app-text: #0f172a;
      --app-muted: #526071;
      --app-border: #d6e0ec;
      --app-accent: #0f766e;
      --app-accent-2: #be123c;
    }
    body.body--dark {
      --app-bg: #0b1220;
      --app-surface: #101826;
      --app-surface-2: #172233;
      --app-text: #e2e8f0;
      --app-muted: #94a3b8;
      --app-border: #223247;
      --app-accent: #14b8a6;
      --app-accent-2: #fb7185;
    }
    body { background: var(--app-bg); color: var(--app-text); }
    .app-header { background: rgba(15, 23, 42, 0.92); backdrop-filter: blur(12px); }
    body.body--dark .app-header { background: rgba(3, 7, 18, 0.96); }
    .app-header .q-btn, .app-header .q-toggle, .app-header .q-field__native, .app-header .text-white { color: #f8fafc; }
    .app-shell { min-height: calc(100vh - 64px); background: var(--app-bg); }
    .app-drawer { background: var(--app-surface); border-right: 1px solid var(--app-border); }
    .drawer-brand { font-size: 0.75rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--app-muted); }
    .drawer-title { font-size: 1.2rem; font-weight: 700; color: var(--app-text); }
    .nav-button { justify-content: flex-start; width: 100%; text-transform: none; border-radius: 12px; padding: 0.35rem 0.5rem; }
    .nav-button-active { background: rgba(15, 118, 110, 0.12); color: var(--app-accent); }
    .content-shell { width: 100%; max-width: none; padding: 1.25rem 1.5rem 1.5rem 1.5rem; gap: 1rem; }
    .page-title { font-size: 1.5rem; font-weight: 700; color: var(--app-text); }
    .page-subtitle { color: var(--app-muted); font-size: 0.95rem; }
    .panel { background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 18px; padding: 1rem 1rem 1.1rem 1rem; }
    .panel-tight { padding: 0.75rem 0.9rem; }
    .section-label { color: var(--app-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.12em; font-weight: 600; }
    .stat-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; width: 100%; }
    .stat-block { border-top: 1px solid var(--app-border); padding-top: 0.75rem; }
    .stat-value { font-size: 1.15rem; font-weight: 700; color: var(--app-text); }
    .toolbar-grid { display: grid; grid-template-columns: 1.3fr 0.9fr 0.9fr 1.1fr auto auto auto; gap: 0.75rem; width: 100%; align-items: end; }
    .toolbar-grid .q-field, .toolbar-grid .q-select, .toolbar-grid .q-input { width: 100%; }
    .results-shell .q-table__middle { max-height: calc(100vh - 300px); }
    .match-table .q-table__middle { overflow-x: hidden; }
    .match-table table { width: 100%; table-layout: fixed; }
    .match-table th, .match-table td { white-space: normal; }
    .match-role-meta { display: flex; flex-wrap: wrap; gap: 0.35rem 0.65rem; margin-top: 0.3rem; font-size: 0.82rem; color: var(--app-muted); }
    .match-role-meta span { white-space: nowrap; }
    .scan-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; justify-content: space-between; width: 100%; }
    .scan-actions-left, .scan-actions-right { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; }
    .scan-grid { display: grid; grid-template-columns: minmax(460px, 1.15fr) minmax(320px, 0.85fr); gap: 1rem; width: 100%; }
    .scan-metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0.75rem; width: 100%; }
    .scan-status-row { display: flex; align-items: center; gap: 0.75rem; }
    .scan-log { height: 320px; overflow-y: auto; background: var(--app-surface-2); border: 1px solid var(--app-border); border-radius: 14px; padding: 0.6rem; }
    .scan-log .q-item__label, .scan-log .text-sm, .scan-log .text-xs { color: var(--app-text); }
    .error-banner { border-radius: 14px; padding: 0.75rem 0.9rem; border: 1px solid rgba(220, 38, 38, 0.25); background: rgba(220, 38, 38, 0.08); color: #b91c1c; }
    body.body--dark .error-banner { color: #fecaca; }
    .info-banner { border-radius: 14px; padding: 0.75rem 0.9rem; border: 1px solid var(--app-border); background: var(--app-surface-2); color: var(--app-text); }
    .recent-scan-table .q-table__middle { max-height: 260px; }
    .results-shell thead tr th { position: sticky; top: 0; z-index: 1; background: var(--app-surface); }
    .score-pill { display: inline-flex; min-width: 3.7rem; justify-content: center; padding: 0.2rem 0.45rem; border-radius: 999px; background: rgba(15, 118, 110, 0.12); color: var(--app-accent); font-size: 0.78rem; }
    .salary-pill { display: inline-flex; padding: 0.2rem 0.45rem; border-radius: 999px; background: rgba(124, 58, 237, 0.12); color: #6d28d9; font-size: 0.78rem; }
    body.body--dark .salary-pill { color: #c4b5fd; }
    .job-primary { font-weight: 600; color: var(--app-text); }
    .job-secondary { color: var(--app-muted); font-size: 0.88rem; }
    .detail-grid { display: grid; grid-template-columns: 1.2fr 0.8fr 1fr; gap: 1rem; }
    .detail-block { padding-top: 0.2rem; }
    .detail-title { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--app-muted); margin-bottom: 0.45rem; }
    .detail-copy { color: var(--app-text); line-height: 1.5; white-space: pre-line; }
    .muted-copy { color: var(--app-muted); }
    .chip-row { display: flex; flex-wrap: wrap; gap: 0.45rem; }
    .skill-chip { display: inline-flex; align-items: center; border-radius: 999px; padding: 0.15rem 0.55rem; background: var(--app-surface-2); color: var(--app-text); font-size: 0.8rem; }
    .resume-copy { min-height: 12rem; }
    .sources-grid { display: grid; grid-template-columns: minmax(320px, 0.95fr) minmax(420px, 1.2fr); gap: 1rem; width: 100%; }
    .settings-grid { display: grid; grid-template-columns: repeat(2, minmax(280px, 1fr)); gap: 1rem; width: 100%; }
    .empty-state { border: 1px dashed var(--app-border); border-radius: 16px; padding: 2rem; color: var(--app-muted); }
    @media (max-width: 1200px) {
      .toolbar-grid { grid-template-columns: 1fr 1fr; }
      .detail-grid { grid-template-columns: 1fr; }
      .stat-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .scan-grid, .scan-metrics { grid-template-columns: 1fr; }
      .sources-grid, .settings-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 768px) {
      .content-shell { padding: 1rem; }
      .stat-strip { grid-template-columns: 1fr; }
    }
    """,
    shared=True,
)

ENGINE = JobMatchEngine()


def _request_token(request: Request, payload: dict[str, Any]) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_token = request.headers.get("x-jobmatch-token", "").strip()
    if header_token:
        return header_token
    body_token = payload.get("token")
    return str(body_token or "").strip()


@app.get("/api/browser-capture/status")
async def browser_capture_status(request: Request) -> dict[str, Any]:
    server_origin = str(request.base_url).rstrip("/")
    return {
        "ok": True,
        "app": APP_NAME,
        "capture_endpoint": "/api/browser-capture",
        "server_origin": server_origin,
        "browser_token": ENGINE.get_browser_api_token(),
        "token_required": True,
    }


@app.post("/api/browser-capture")
async def browser_capture_import(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Capture payload must be a JSON object.")
    token = _request_token(request, payload)
    if token != ENGINE.get_browser_api_token():
        raise HTTPException(status_code=401, detail="Invalid browser capture token.")
    try:
        return await asyncio.to_thread(ENGINE.import_browser_capture, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@dataclass(slots=True)
class UIState:
    current_view: str = "dashboard"
    location_query: str = ""
    remote_mode: str = "any"
    job_type: str = "any"
    clearance_terms: list[str] = field(default_factory=list)
    matches: list[MatchResult] = field(default_factory=list)
    match_error: str = ""
    scan_running: bool = False
    scan_stop_requested: bool = False
    scan_status: str = "Ready"
    scan_error: str = ""
    scan_source_total: int = 0
    scan_sources_finished: int = 0
    scan_rows: list[dict[str, Any]] = field(default_factory=list)
    scan_log_lines: list[str] = field(default_factory=list)
    recent_scans: list[dict[str, Any]] = field(default_factory=list)
    last_scan_text: str = "Never"
    selected_source_id: int | None = None
    manual_job_urls: str = ""
    discovery_query: str = ""
    discovery_results: list[dict[str, Any]] = field(default_factory=list)
    source_form: dict[str, Any] = field(
        default_factory=lambda: {
            "id": None,
            "name": "",
            "source_type": "auto",
            "url": "",
            "identifier": "",
            "enabled": True,
            "use_playwright": False,
            "use_browser_profile": False,
            "refresh_minutes": 180,
            "max_pages": DEFAULT_SOURCE_MAX_PAGES,
            "request_delay_ms": DEFAULT_SOURCE_REQUEST_DELAY_MS,
            "notes": "",
        }
    )


class JobMatchUI:
    def __init__(self, engine: JobMatchEngine) -> None:
        self.engine = engine
        self.state = UIState()
        self.dark_mode = ui.dark_mode(self._theme_setting_to_value(str(self.engine.get_settings().get("theme_mode", "auto"))))
        self.client = None
        self.content = None
        self.nav_shell = None
        self.status_label = None
        self.scan_button = None
        self.stop_scan_button = None
        self.clear_results_button = None
        self.scan_summary_panel = None
        self.scan_log_panel = None
        self.scan_log_widget = None
        self._client_deleted = False
        self._background_tasks: list[asyncio.Task] = []
        self.source_inputs: dict[str, Any] = {}
        self.settings_inputs: dict[str, Any] = {}
        self.browser_token_input = None

    def mount(self) -> None:
        self.client = ui.context.client
        self.client.on_delete(self._handle_client_delete)
        with ui.header(elevated=False, bordered=False).classes("app-header px-4 py-2 items-center"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(APP_NAME).classes("text-white text-lg font-bold")
                        ui.label("Local resume-to-job matcher").classes("text-slate-300 text-xs tracking-wide uppercase")
                    self.status_label = ui.label("Ready").classes("text-slate-200 text-sm")
                with ui.row().classes("items-center gap-3"):
                    ui.label("Browser capture ready").classes("text-slate-300 text-sm")

        with ui.left_drawer(value=True, bordered=False, elevated=False).classes("app-drawer w-72"):
            self.nav_shell = ui.column().classes("w-full gap-3 p-4")
            self.render_sidebar()

        with ui.column().classes("app-shell w-full"):
            self.content = ui.column().classes("content-shell")
            self.render_current_view()
            self._start_background_task(self._bootstrap_after_mount())
            self._start_background_task(self._schedule_loop())

    async def _bootstrap_after_mount(self) -> None:
        await asyncio.sleep(0.2)
        if self._client_deleted:
            return
        await self.bootstrap()

    async def _schedule_loop(self) -> None:
        while not self._client_deleted:
            await asyncio.sleep(60.0)
            if self._client_deleted:
                break
            await self.schedule_tick()

    def _start_background_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.append(task)

        def cleanup(done_task: asyncio.Task) -> None:
            if done_task in self._background_tasks:
                self._background_tasks.remove(done_task)
            if done_task.cancelled():
                return
            try:
                done_task.result()
            except Exception:
                return

        task.add_done_callback(cleanup)

    def _notify(
        self,
        message: Any,
        *,
        position: str = "bottom",
        close_button: bool | str = False,
        type: str | None = None,
        color: str | None = None,
        multi_line: bool = False,
        **kwargs: Any,
    ) -> None:
        if self._client_deleted or self.client is None:
            return
        options = {
            "message": str(message),
            "position": position,
            "closeBtn": close_button,
            "multiLine": multi_line,
        }
        if type is not None:
            options["type"] = type
        if color is not None:
            options["color"] = color
        options.update(kwargs)
        self.client.outbox.enqueue_message("notify", options, self.client.id)

    def _handle_client_delete(self) -> None:
        self._client_deleted = True
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

    @staticmethod
    def _theme_setting_to_value(theme_mode: str) -> bool | None:
        if theme_mode == "dark":
            return True
        if theme_mode == "light":
            return False
        return None

    def _apply_theme_mode(self, theme_mode: str) -> None:
        value = self._theme_setting_to_value(theme_mode)
        if value is None:
            self.dark_mode.auto()
        else:
            self.dark_mode.set_value(value)

    def _sync_last_scan_text(self) -> None:
        scans = self.engine.list_recent_scans(limit=1)
        self.state.recent_scans = self.engine.list_recent_scans(limit=8)
        if scans and scans[0].get("finished_at"):
            finished_at = scans[0]["finished_at"]
            self.state.last_scan_text = finished_at.strftime("%Y-%m-%d %H:%M")
        else:
            self.state.last_scan_text = "Never"

    async def bootstrap(self) -> None:
        if self._client_deleted:
            return
        self._apply_theme_mode(str(self.engine.get_settings().get("theme_mode", "auto")))
        self._sync_last_scan_text()
        if self.engine.get_active_resume():
            try:
                self.state.matches = await asyncio.to_thread(self.engine.get_ranked_matches, self.current_filters())
                self.state.match_error = ""
            except Exception as exc:
                self.state.match_error = str(exc)
                self.state.matches = []
                self._append_activity(f"Initial ranking failed: {exc}")
        else:
            self.state.matches = []
            self.state.match_error = ""
        self.render_current_view()

    def render_sidebar(self) -> None:
        if self._client_deleted or self.nav_shell is None:
            return
        self.nav_shell.clear()
        with self.nav_shell:
            ui.label("Workspace").classes("drawer-brand")
            ui.label(APP_NAME).classes("drawer-title mb-2")
            items = [
                ("dashboard", "Dashboard", "table_view"),
                ("scans", "Scans", "sync"),
                ("sources", "Sources", "travel_explore"),
                ("resume", "Resume", "description"),
                ("settings", "Settings", "tune"),
            ]
            for key, label, icon in items:
                classes = "nav-button nav-button-active" if self.state.current_view == key else "nav-button"
                ui.button(label, icon=icon, on_click=lambda _, target=key: self.set_view(target)).props("flat no-caps align=left").classes(classes)
            ui.separator().classes("my-2")
            sources = self.engine.list_sources()
            ui.label(f"{len(sources)} source(s) configured").classes("text-sm muted-copy")
            resume = self.engine.get_active_resume()
            ui.label(resume.filename if resume else "No resume loaded").classes("text-sm")

    def set_view(self, view: str) -> None:
        self.state.current_view = view
        self.render_sidebar()
        self.render_current_view()

    def render_current_view(self) -> None:
        if self._client_deleted or self.content is None:
            return
        self.scan_button = None
        self.stop_scan_button = None
        self.clear_results_button = None
        self.scan_summary_panel = None
        self.scan_log_panel = None
        self.scan_log_widget = None
        self.content.clear()
        with self.content:
            if self.state.current_view == "dashboard":
                self.render_dashboard()
            elif self.state.current_view == "scans":
                self.render_scans()
            elif self.state.current_view == "sources":
                self.render_sources()
            elif self.state.current_view == "resume":
                self.render_resume()
            else:
                self.render_settings()

    def render_dashboard(self) -> None:
        resume = self.engine.get_active_resume()
        sources = self.engine.list_sources()
        cached_jobs = len(self.engine.storage.list_jobs())
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Dashboard").classes("page-title")
                    ui.label("Ranked matches, filters, and exports in one working surface.").classes("page-subtitle")
                with ui.row().classes("gap-2"):
                    ui.button("Rank jobs", icon="auto_awesome", on_click=lambda: asyncio.create_task(self.refresh_matches())).props("unelevated")
                    ui.button("Export CSV", icon="download", on_click=lambda: self.handle_export("csv")).props("flat")
                    ui.button("Export JSON", icon="code", on_click=lambda: self.handle_export("json")).props("flat")

            with ui.element("section").classes("panel panel-tight"):
                with ui.element("div").classes("toolbar-grid"):
                    ui.input("Location", value=self.state.location_query, on_change=lambda e: setattr(self.state, "location_query", e.value)).props("outlined dense")
                    ui.select(REMOTE_MODES, value=self.state.remote_mode, label="Remote", on_change=lambda e: setattr(self.state, "remote_mode", e.value)).props("outlined dense")
                    ui.select(JOB_TYPES, value=self.state.job_type, label="Job type", on_change=lambda e: setattr(self.state, "job_type", e.value)).props("outlined dense")
                    ui.select(
                        list(CLEARANCE_PATTERNS.keys()),
                        value=self.state.clearance_terms,
                        label="Clearance",
                        multiple=True,
                        clearable=True,
                        with_input=True,
                        new_value_mode="add-unique",
                        on_change=lambda e: setattr(self.state, "clearance_terms", list(e.value or [])),
                    ).props("outlined dense use-chips")
                    ui.button("Apply", icon="filter_alt", on_click=lambda: asyncio.create_task(self.refresh_matches())).props("unelevated")
                    ui.button("Clear", icon="restart_alt", on_click=self.clear_filters).props("flat")
                    ui.label(f"{len(self.state.matches)} result(s)").classes("self-center muted-copy text-right")

            with ui.element("section").classes("panel"):
                with ui.element("div").classes("stat-strip"):
                    self._stat_block("Resume", resume.filename if resume else "None loaded")
                    self._stat_block("Sources", str(len(sources)))
                    self._stat_block("Cached jobs", str(cached_jobs))
                    self._stat_block("Matches", str(len(self.state.matches)))

            with ui.element("section").classes("panel results-shell"):
                if self.state.match_error and not self.state.matches:
                    self._empty_state(self.state.match_error)
                elif not resume:
                    self._empty_state("Upload a resume to start matching against the cached jobs.")
                elif not sources:
                    self._empty_state("Add at least one source so the scan engine has something to pull from.")
                elif not self.state.matches:
                    self._empty_state("No matches yet. Import jobs from the extension or run a scheduled scan, then rank the jobs.")
                else:
                    self._render_results_table(self.state.matches)

    def render_scans(self) -> None:
        sources = self.engine.list_sources()
        scanable_sources = self.engine.list_scanable_sources()
        manual_sources = [source for source in sources if self.engine.is_manual_assist_source(source)]
        settings = self.engine.get_settings()
        cadence = f"{int(settings.get('scheduler_interval_minutes', 180))} min" if settings.get("scheduler_enabled") else "Off"
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Scans").classes("page-title")
                    ui.label("Run scheduled sources, review scan outcomes, and clear cached scan state.").classes("page-subtitle")

            with ui.element("section").classes("panel panel-tight"):
                with ui.element("div").classes("scan-actions"):
                    with ui.element("div").classes("scan-actions-left"):
                        self.scan_button = ui.button(
                            "Scan now",
                            icon="sync",
                            on_click=lambda: asyncio.create_task(self.handle_scan()),
                        ).props("unelevated")
                        self.stop_scan_button = ui.button(
                            "Stop scan",
                            icon="stop_circle",
                            on_click=self.request_scan_stop,
                        ).props("flat color=negative")
                        self.clear_results_button = ui.button(
                            "Start fresh",
                            icon="delete_sweep",
                            on_click=lambda: asyncio.create_task(self.clear_scan_results()),
                        ).props("flat color=warning")
                    with ui.element("div").classes("scan-actions-right"):
                        ui.label(f"Last scan: {self.state.last_scan_text}").classes("muted-copy text-sm")
                if self.state.scan_running:
                    if self.scan_button is not None:
                        self.scan_button.disable()
                    if self.clear_results_button is not None:
                        self.clear_results_button.disable()
                elif self.stop_scan_button is not None:
                    self.stop_scan_button.disable()

            with ui.element("section").classes("panel"):
                with ui.element("div").classes("stat-strip"):
                    self._stat_block("Scanable sources", str(len(scanable_sources)))
                    self._stat_block("Manual assist", str(len(manual_sources)))
                    self._stat_block("Scheduler", cadence)
                    self._stat_block("Last scan", self.state.last_scan_text)

            if not scanable_sources:
                message = "No scheduled scan sources are enabled. Browser-capture sources refresh from the extension instead of Scan now."
                if not sources:
                    message = "No sources are configured yet. Add ATS boards in Sources or import jobs from the browser extension first."
                ui.label(message).classes("info-banner text-sm")

            with ui.element("div").classes("scan-grid"):
                with ui.element("section").classes("panel"):
                    self.scan_summary_panel = ui.column().classes("w-full gap-3")
                    self.render_scan_summary_panel()
                with ui.element("section").classes("panel"):
                    self.scan_log_panel = ui.column().classes("w-full gap-3")
                    self.render_scan_log_panel()

    def render_sources(self) -> None:
        sources = self.engine.list_sources()
        selected_source = next((source for source in sources if source.id == self.state.selected_source_id), None)
        manual_assist = bool(selected_source and self.engine.is_manual_assist_source(selected_source))
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Sources").classes("page-title")
                    ui.label("Manage ATS boards, discovery targets, and browser-assisted source inputs in one place.").classes("page-subtitle")
                with ui.row().classes("gap-2"):
                    ui.button("New source", icon="add", on_click=self.reset_source_form).props("unelevated")
                    ui.button("Save source", icon="save", on_click=self.save_source_form).props("unelevated")
                    ui.button("Delete source", icon="delete", on_click=self.delete_selected_source).props("flat")

            with ui.element("section").classes("panel"):
                with ui.row().classes("w-full items-end gap-3"):
                    ui.input(
                        "Discover from company name, homepage, or careers URL",
                        value=self.state.discovery_query,
                        on_change=lambda e: setattr(self.state, "discovery_query", e.value or ""),
                    ).props("outlined").classes("w-full")
                    ui.button(
                        "Discover Source",
                        icon="travel_explore",
                        on_click=lambda: asyncio.create_task(self.handle_source_discovery()),
                    ).props("unelevated")
                ui.label(
                    "Discovery looks for ATS boards like Greenhouse, Lever, Ashby, Workday, and company careers pages so you do not need to know those URLs ahead of time."
                ).classes("mt-3 muted-copy text-sm")
                if self.state.discovery_results:
                    with ui.column().classes("w-full gap-2 mt-4"):
                        for index, candidate in enumerate(self.state.discovery_results):
                            with ui.row().classes("w-full items-start justify-between gap-3 panel panel-tight"):
                                with ui.column().classes("gap-1"):
                                    ui.label(candidate["name"]).classes("font-semibold")
                                    ui.label(
                                        f"{candidate['platform']} -> {candidate['source_type']} | {candidate['reason']}"
                                    ).classes("muted-copy text-sm")
                                    ui.label(candidate["url"]).classes("mono text-xs")
                                with ui.row().classes("gap-2"):
                                    ui.button(
                                        "Load",
                                        icon="arrow_downward",
                                        on_click=lambda _, idx=index: self.load_discovered_candidate(idx),
                                    ).props("flat")
                                    ui.button(
                                        "Save",
                                        icon="save",
                                        on_click=lambda _, idx=index: self.save_discovered_candidate(idx),
                                    ).props("unelevated")

            with ui.element("div").classes("sources-grid"):
                with ui.element("section").classes("panel"):
                    rows = [
                        {
                            "id": source.id,
                            "name": source.name,
                            "type": source.source_type,
                            "status": source.last_status or "never scanned",
                            "url": source.url,
                        }
                        for source in sources
                    ]
                    table = ui.table(
                        rows=rows,
                        row_key="id",
                        selection="single",
                        columns=[
                            {"name": "name", "label": "Name", "field": "name", "sortable": True},
                            {"name": "type", "label": "Type", "field": "type", "sortable": True},
                            {"name": "status", "label": "Last status", "field": "status"},
                        ],
                        pagination=10,
                        on_select=lambda e: self.select_source((e.selection or [None])[0]),
                    ).classes("w-full")
                    if selected_source:
                        table.selected = [row for row in rows if row["id"] == selected_source.id]
                        table.update()

                with ui.element("section").classes("panel"):
                    form = self.state.source_form
                    with ui.grid(columns=2).classes("w-full gap-3"):
                        self.source_inputs["name"] = ui.input("Name", value=form["name"]).props("outlined")
                        self.source_inputs["source_type"] = ui.select(SOURCE_TYPES, value=form["source_type"], label="Source type").props("outlined")
                        self.source_inputs["url"] = ui.input("URL or search page", value=form["url"]).props("outlined").classes("col-span-2")
                        self.source_inputs["identifier"] = ui.input("API identifier", value=form["identifier"]).props("outlined")
                        self.source_inputs["refresh_minutes"] = ui.number("Refresh minutes", value=form["refresh_minutes"], min=15, step=15).props("outlined")
                        self.source_inputs["max_pages"] = ui.number("Max pages", value=form["max_pages"], min=1, step=1).props("outlined")
                        self.source_inputs["request_delay_ms"] = ui.number("Request delay (ms)", value=form["request_delay_ms"], min=0, step=250).props("outlined")
                        self.source_inputs["enabled"] = ui.switch("Enabled", value=form["enabled"])
                        self.source_inputs["use_playwright"] = ui.switch("Use Playwright fallback", value=form["use_playwright"])
                        self.source_inputs["use_browser_profile"] = ui.switch(
                            "Use persistent browser profile", value=form["use_browser_profile"]
                        )
                    self.source_inputs["notes"] = ui.textarea("Notes", value=form["notes"]).props("outlined autogrow").classes("w-full mt-3")
                    if selected_source:
                        ui.label(f"Editing source #{selected_source.id}").classes("mt-3 text-sm muted-copy")
                        if manual_assist:
                            helper_copy = "This source is treated as manual-assist and is excluded from periodic refresh."
                            if selected_source.source_type == "browser_capture":
                                helper_copy = (
                                    "This source is managed by browser capture. Refresh it from the extension instead of Scan now."
                                )
                            ui.label(helper_copy).classes("mt-2 muted-copy text-sm")
                        ui.separator().classes("my-4")
                        with ui.row().classes("w-full gap-2 items-center"):
                            ui.button("Open In Browser", icon="open_in_new", on_click=self.open_selected_source_browser).props(
                                "unelevated"
                            )
                            ui.button(
                                "Import Source Page",
                                icon="download_for_offline",
                                on_click=lambda: asyncio.create_task(self.handle_manual_source_import()),
                            ).props("flat")
                        ui.upload(
                            label="Upload saved HTML",
                            auto_upload=True,
                            on_upload=lambda e: asyncio.create_task(self.handle_source_html_upload(e)),
                            on_rejected=lambda: self._notify("Saved HTML upload rejected.", type="negative"),
                        ).props("accept=.html,.htm,text/html bordered").classes("w-full mt-3")
                        self.source_inputs["manual_job_urls"] = ui.textarea(
                            "Paste job URLs",
                            value=self.state.manual_job_urls,
                            on_change=lambda e: setattr(self.state, "manual_job_urls", e.value or ""),
                        ).props("outlined autogrow").classes("w-full mt-3")
                        ui.button(
                            "Import URLs",
                            icon="link",
                            on_click=lambda: asyncio.create_task(self.handle_job_url_import()),
                        ).props("flat")

    def render_resume(self) -> None:
        resume = self.engine.get_active_resume()
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Resume").classes("page-title")
                    ui.label("Upload a PDF or DOCX once, then reuse the parsed profile across scans.").classes("page-subtitle")

            with ui.grid(columns=2).classes("w-full gap-4"):
                with ui.element("section").classes("panel"):
                    ui.upload(
                        label="Upload resume",
                        auto_upload=True,
                        max_file_size=20_000_000,
                        on_upload=lambda e: asyncio.create_task(self.handle_resume_upload(e)),
                        on_rejected=lambda: self._notify("Resume upload rejected.", type="negative"),
                    ).props("accept=.pdf,.docx,.txt bordered").classes("w-full")
                    ui.label("Supported formats: PDF and DOCX. The active resume is parsed and stored locally.").classes("mt-3 muted-copy")

                with ui.element("section").classes("panel"):
                    if not resume:
                        self._empty_state("No resume is active yet.")
                    else:
                        ui.label(resume.filename).classes("text-lg font-semibold")
                        ui.label(f"Estimated experience: {resume.experience_years:.1f} years").classes("muted-copy")
                        if resume.recent_titles:
                            ui.label("Recent titles").classes("section-label mt-3")
                            with ui.element("div").classes("chip-row mt-2"):
                                for title in resume.recent_titles[:8]:
                                    ui.html(f'<span class="skill-chip">{title}</span>', sanitize=False)
                        ui.label("Skills").classes("section-label mt-3")
                        with ui.element("div").classes("chip-row mt-2"):
                            for skill in resume.skills[:24]:
                                ui.html(f'<span class="skill-chip">{skill}</span>', sanitize=False)
                        ui.label("Tools").classes("section-label mt-4")
                        with ui.element("div").classes("chip-row mt-2"):
                            for tool in resume.tools[:20]:
                                ui.html(f'<span class="skill-chip">{tool}</span>', sanitize=False)
                        if resume.certifications:
                            ui.label("Certifications").classes("section-label mt-4")
                            with ui.element("div").classes("chip-row mt-2"):
                                for certification in resume.certifications[:16]:
                                    ui.html(f'<span class="skill-chip">{certification}</span>', sanitize=False)
                        if resume.clearance_terms:
                            ui.label("Clearance").classes("section-label mt-4")
                            with ui.element("div").classes("chip-row mt-2"):
                                for clearance in resume.clearance_terms:
                                    ui.html(f'<span class="skill-chip">{clearance}</span>', sanitize=False)
                        ui.label("Parsed summary").classes("section-label mt-4")
                        ui.textarea(value=resume.summary_text, label=None).props("outlined readonly autogrow").classes("w-full resume-copy")

    def render_settings(self) -> None:
        settings = self.engine.get_settings()
        ollama_status = self.engine.get_ollama_status()
        browser_token = self.engine.get_browser_api_token()
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-end justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Settings").classes("page-title")
                    ui.label("Tune theme, scan cadence, and the hybrid score weights without changing code.").classes("page-subtitle")
                ui.button("Save settings", icon="save", on_click=self.save_settings).props("unelevated")

            with ui.element("div").classes("settings-grid"):
                with ui.element("section").classes("panel"):
                    self.settings_inputs["theme_mode"] = ui.select(
                        THEME_MODES,
                        value=str(settings.get("theme_mode", "auto")),
                        label="Theme mode",
                    ).props("outlined").classes("w-full")
                    ui.label("Auto follows the browser or operating system color preference by default.").classes(
                        "mt-2 muted-copy text-sm"
                    )
                    self.settings_inputs["scheduler_enabled"] = ui.switch("Enable periodic refresh", value=bool(settings.get("scheduler_enabled", False)))
                    ui.label("Periodic refresh skips manual-assist sources that use browser profiles or Indeed searches.").classes(
                        "mt-2 muted-copy text-sm"
                    )
                    self.settings_inputs["scheduler_interval_minutes"] = ui.number(
                        "Refresh interval (minutes)",
                        value=int(settings.get("scheduler_interval_minutes", 180)),
                        min=15,
                        step=15,
                    ).props("outlined").classes("w-full mt-3")
                    self.settings_inputs["max_source_jobs"] = ui.number(
                        "Max jobs per source",
                        value=int(settings.get("max_source_jobs", 120)),
                        min=10,
                        step=10,
                    ).props("outlined").classes("w-full mt-3")
                    self.settings_inputs["embedding_model_name"] = ui.input(
                        "Embedding model",
                        value=str(settings.get("embedding_model_name")),
                    ).props("outlined").classes("w-full mt-3")

                with ui.element("section").classes("panel"):
                    self.settings_inputs["embedding_weight"] = ui.number(
                        "Embedding weight",
                        value=float(settings.get("embedding_weight", 0.68)),
                        min=0,
                        max=1,
                        step=0.01,
                    ).props("outlined").classes("w-full")
                    self.settings_inputs["skill_weight"] = ui.number(
                        "Skill weight",
                        value=float(settings.get("skill_weight", 0.22)),
                        min=0,
                        max=1,
                        step=0.01,
                    ).props("outlined").classes("w-full mt-3")
                    self.settings_inputs["experience_weight"] = ui.number(
                        "Experience weight",
                        value=float(settings.get("experience_weight", 0.10)),
                        min=0,
                        max=1,
                        step=0.01,
                    ).props("outlined").classes("w-full mt-3")
                    ui.label("Weights are normalized automatically, so they do not need to sum to 1.0.").classes("mt-3 muted-copy")

                with ui.element("section").classes("panel"):
                    ui.label("Ollama").classes("text-lg font-semibold")
                    if ollama_status.available:
                        status_text = f"Connected to {settings.get('ollama_base_url')} with model {settings.get('ollama_model_name')}."
                    elif ollama_status.running:
                        status_text = (
                            f"Ollama is reachable at {settings.get('ollama_base_url')}, but model "
                            f"{settings.get('ollama_model_name')} is not loaded."
                        )
                    else:
                        status_text = f"Ollama is not reachable at {settings.get('ollama_base_url')}: {ollama_status.error}"
                    ui.label(status_text).classes("mt-1 page-subtitle")
                    if ollama_status.models:
                        ui.label(f"Available models: {', '.join(ollama_status.models[:8])}").classes("mt-2 muted-copy text-sm")
                    else:
                        ui.label("Available models: none reported by the local runtime.").classes("mt-2 muted-copy text-sm")
                    self.settings_inputs["ollama_enabled"] = ui.switch(
                        "Enable Ollama refinement",
                        value=bool(settings.get("ollama_enabled", False)),
                    ).classes("mt-3")
                    self.settings_inputs["ollama_base_url"] = ui.input(
                        "Ollama base URL",
                        value=str(settings.get("ollama_base_url")),
                    ).props("outlined").classes("w-full mt-3")
                    self.settings_inputs["ollama_model_name"] = ui.input(
                        "Ollama model",
                        value=str(settings.get("ollama_model_name")),
                    ).props("outlined").classes("w-full mt-3")
                    self.settings_inputs["ollama_enhance_resume"] = ui.switch(
                        "Use Ollama for resume refinement",
                        value=bool(settings.get("ollama_enhance_resume", True)),
                    ).classes("mt-3")
                    self.settings_inputs["ollama_enhance_jobs"] = ui.switch(
                        "Use Ollama for job refinement",
                        value=bool(settings.get("ollama_enhance_jobs", True)),
                    ).classes("mt-2")
                    self.settings_inputs["ollama_max_job_enrichments"] = ui.number(
                        "Max Ollama job enrichments per scan/import",
                        value=int(settings.get("ollama_max_job_enrichments", 20)),
                        min=0,
                        step=1,
                    ).props("outlined").classes("w-full mt-3")
                    ui.label(
                        "Ollama refinement is optional and local-only. It runs only when enabled and a model is actually loaded."
                    ).classes("mt-3 muted-copy text-sm")

                with ui.element("section").classes("panel"):
                    ui.label("Browser Capture").classes("text-lg font-semibold")
                    ui.label(
                        "Use the extension on the pages you actually browse, then send visible jobs into this local app."
                    ).classes("mt-1 page-subtitle")
                    ui.input("Extension server URL", value="Use the same JobMatch URL you already opened").props(
                        "outlined readonly"
                    ).classes("w-full mt-3")
                    ui.input("Capture endpoint path", value="/api/browser-capture").props("outlined readonly").classes("w-full mt-3")
                    self.browser_token_input = ui.input("Extension token", value=browser_token).props("outlined readonly").classes(
                        "w-full mt-3"
                    )
                    with ui.row().classes("w-full gap-2 mt-3"):
                        ui.button("Rotate token", icon="autorenew", on_click=self.rotate_browser_api_token).props("flat")
                    ui.label(
                        "The extension folder lives in browser_extension/. Browser-capture sources are manual-only and skipped by the scheduler."
                    ).classes("mt-3 muted-copy text-sm")

    def render_scan_summary_panel(self) -> None:
        if self._client_deleted or self.scan_summary_panel is None:
            return
        self.scan_summary_panel.clear()
        totals = self._scan_totals()
        with self.scan_summary_panel:
            with ui.row().classes("w-full items-start justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Scan Summary").classes("text-lg font-semibold")
                    ui.label("Live status, per-source results, and the latest scan outcome.").classes("page-subtitle")
                with ui.row().classes("scan-status-row"):
                    if self.state.scan_running:
                        ui.spinner(size="sm", color="primary")
                    ui.label(self.state.scan_status).classes("font-medium")

            if self.state.scan_error:
                ui.label(f"Scan error: {self.state.scan_error}").classes("error-banner text-sm")
            elif self.state.match_error:
                ui.label(f"Last ranking error: {self.state.match_error}").classes("error-banner text-sm")
            elif not self.state.scan_rows and self.state.recent_scans:
                ui.label("No scan has run in this browser session yet. Recent stored activity is shown below.").classes(
                    "info-banner text-sm"
                )

            with ui.element("div").classes("scan-metrics"):
                self._stat_block("Progress", f"{self.state.scan_sources_finished}/{max(self.state.scan_source_total, len(self.state.scan_rows)) or 0}")
                self._stat_block("New", str(totals["created"]))
                self._stat_block("Updated", str(totals["updated"]))
                self._stat_block("Unchanged", str(totals["unchanged"]))
                self._stat_block("Issues", str(totals["issues"]))

            if self.state.scan_rows:
                rows = [self._scan_row_payload(row) for row in self.state.scan_rows]
                ui.table(
                    rows=rows,
                    row_key="source",
                    columns=[
                        {"name": "source", "label": "Source", "field": "source"},
                        {"name": "status", "label": "Status", "field": "status"},
                        {"name": "pages", "label": "Pages", "field": "pages"},
                        {"name": "jobs", "label": "Jobs", "field": "jobs"},
                        {"name": "changes", "label": "Changes", "field": "changes"},
                        {"name": "note", "label": "Notes", "field": "note"},
                    ],
                    pagination={"rowsPerPage": 6},
                ).classes("w-full recent-scan-table").props("flat dense square separator=horizontal")
            elif self.state.recent_scans:
                rows = [self._recent_scan_row(scan) for scan in self.state.recent_scans[:8]]
                ui.table(
                    rows=rows,
                    row_key="id",
                    columns=[
                        {"name": "source", "label": "Source", "field": "source"},
                        {"name": "status", "label": "Status", "field": "status"},
                        {"name": "finished", "label": "Finished", "field": "finished"},
                        {"name": "changes", "label": "Changes", "field": "changes"},
                    ],
                    pagination={"rowsPerPage": 6},
                ).classes("w-full recent-scan-table").props("flat dense square separator=horizontal")
            else:
                self._empty_state("No scan activity yet. Add sources, then run a scan.")

    def render_scan_log_panel(self) -> None:
        if self._client_deleted or self.scan_log_panel is None:
            return
        self.scan_log_panel.clear()
        with self.scan_log_panel:
            with ui.row().classes("w-full items-start justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Activity Log").classes("text-lg font-semibold")
                    ui.label("High-level events from scans and ranking runs.").classes("page-subtitle")
                ui.label(datetime.now().strftime("%H:%M")).classes("muted-copy text-sm")

            if not self.state.scan_log_lines:
                self._empty_state("No activity logged yet.")
                self.scan_log_widget = None
                return

            self.scan_log_widget = ui.log(max_lines=160).classes("w-full scan-log")
            for line in self.state.scan_log_lines[-160:]:
                self.scan_log_widget.push(line, classes="mono text-xs")

    async def refresh_matches(self, *, record_activity: bool = True) -> None:
        if self._client_deleted:
            return
        self.status_label.set_text("Ranking jobs...")
        if record_activity:
            self._append_activity("Ranking cached jobs against the active resume.")
        try:
            self.state.matches = await asyncio.to_thread(self.engine.get_ranked_matches, self.current_filters())
            self.state.match_error = ""
            if record_activity:
                self._append_activity(f"Ranking finished: {len(self.state.matches)} match(es) ready.")
        except Exception as exc:
            self.state.matches = []
            self.state.match_error = str(exc)
            if record_activity:
                self._append_activity(f"Ranking failed: {exc}")
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")
            self.render_scan_summary_panel()
            self.render_scan_log_panel()
            self.render_current_view()

    async def handle_scan(self, *, background: bool = False) -> None:
        if self._client_deleted:
            return
        if self.state.scan_running:
            self._append_activity("Scan request ignored because a scan is already running.")
            self.render_scan_log_panel()
            return
        if not self.engine.list_scanable_sources():
            self._append_activity("Scan skipped because all enabled sources are browser-capture only.")
            self._notify(
                "No scanable sources are enabled. Browser-capture sources are refreshed from the extension.",
                type="warning",
            )
            self.render_scan_log_panel()
            return
        if self.scan_button:
            self.scan_button.disable()
        if self.stop_scan_button:
            self.stop_scan_button.enable()
        if self.clear_results_button:
            self.clear_results_button.disable()
        self.state.scan_running = True
        self.state.scan_stop_requested = False
        self.state.scan_error = ""
        self.state.scan_status = "Scanning sources..."
        self.state.scan_source_total = 0
        self.state.scan_sources_finished = 0
        self.state.scan_rows = []
        self.status_label.set_text("Scanning sources...")
        self.render_scan_summary_panel()
        self.render_scan_log_panel()
        try:
            summary = await self.engine.scan_sources(on_progress=self._handle_scan_progress)
            self._apply_scan_summary(summary)
            self.state.scan_running = False
            self.state.scan_stop_requested = False
            self.state.scan_status = self._scan_complete_status(summary)
            if summary.finished_at:
                self.state.last_scan_text = summary.finished_at.strftime("%Y-%m-%d %H:%M")
            self.state.recent_scans = self.engine.list_recent_scans(limit=8)
            await self.refresh_matches(record_activity=True)
        except Exception as exc:
            self.state.scan_running = False
            self.state.scan_stop_requested = False
            self.state.scan_error = str(exc)
            self.status_label.set_text("Scan failed")
            self.state.scan_status = "Scan failed"
            self._append_activity(f"Scan failed: {exc}")
        finally:
            if self.scan_button:
                self.scan_button.enable()
            if self.stop_scan_button:
                self.stop_scan_button.disable()
            if self.clear_results_button:
                self.clear_results_button.enable()
            self.state.recent_scans = self.engine.list_recent_scans(limit=8)
            if self.status_label.text == "Scanning sources...":
                self.status_label.set_text("Ready")
            self.render_scan_summary_panel()
            self.render_scan_log_panel()

    def request_scan_stop(self) -> None:
        if not self.state.scan_running or self.state.scan_stop_requested:
            return
        if not self.engine.cancel_scan():
            self._append_activity("Stop request ignored because no scan is currently active.")
            self.render_scan_log_panel()
            return
        self.state.scan_stop_requested = True
        self.state.scan_status = "Stopping scan after current request..."
        if self.status_label is not None:
            self.status_label.set_text(self.state.scan_status)
        if self.stop_scan_button is not None:
            self.stop_scan_button.disable()
        self._append_activity("Stop requested. Finishing the current request and cancelling remaining source work.")
        self.render_scan_summary_panel()
        self.render_scan_log_panel()

    async def schedule_tick(self) -> None:
        if self._client_deleted:
            return
        if self.engine.should_run_scheduled_scan():
            await self.handle_scan(background=True)

    async def clear_scan_results(self) -> None:
        if self._client_deleted:
            return
        if self.state.scan_running:
            self._notify("Stop the active scan before clearing cached results.", type="warning")
            return
        self.status_label.set_text("Clearing cached results...")
        try:
            await asyncio.to_thread(self.engine.clear_scan_results)
            self.state.matches = []
            self.state.match_error = ""
            self.state.scan_error = ""
            self.state.scan_status = "Ready"
            self.state.scan_source_total = 0
            self.state.scan_sources_finished = 0
            self.state.scan_rows = []
            self.state.recent_scans = []
            self.state.scan_log_lines = []
            self.state.last_scan_text = "Never"
            self._append_activity("Cleared cached jobs, scan history, and source cache state.")
            self._notify("Cleared cached jobs and scan history.", type="positive")
        except Exception as exc:
            self._append_activity(f"Clearing cached results failed: {exc}")
            self._notify(f"Could not clear cached results: {exc}", type="negative")
        finally:
            self.status_label.set_text("Ready")
            self.render_current_view()

    async def handle_resume_upload(self, event: events.UploadEventArguments) -> None:
        if self._client_deleted:
            return
        suffix = Path(event.file.name).suffix or ".pdf"
        incoming_path = UPLOADS_DIR / f"incoming-{safe_filename(Path(event.file.name).stem, suffix)}"
        await event.file.save(incoming_path)
        self.status_label.set_text("Loading resume...")
        self._append_activity(f"Importing resume {event.file.name}.")
        try:
            resume = await asyncio.to_thread(self.engine.save_resume, incoming_path)
            self._append_activity(f"Loaded resume: {resume.filename}.")
            self.render_sidebar()
            await self.refresh_matches(record_activity=True)
        except Exception as exc:
            self.state.match_error = str(exc)
            self._append_activity(f"Resume import failed: {exc}")
            self.render_scan_summary_panel()
            self.render_scan_log_panel()
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")

    def select_source(self, row: dict | None) -> None:
        if not row:
            self.state.selected_source_id = None
            self.state.manual_job_urls = ""
            self.reset_source_form()
            return
        source = self.engine.get_source(int(row["id"]))
        if source is None:
            return
        self.state.selected_source_id = source.id
        self.state.manual_job_urls = ""
        self.state.source_form = {
            "id": source.id,
            "name": source.name,
            "source_type": source.source_type,
            "url": source.url,
            "identifier": source.identifier or "",
            "enabled": source.enabled,
            "use_playwright": source.use_playwright,
            "use_browser_profile": source.use_browser_profile,
            "refresh_minutes": source.refresh_minutes,
            "max_pages": source.max_pages,
            "request_delay_ms": source.request_delay_ms,
            "notes": source.notes,
        }
        self.render_current_view()

    def reset_source_form(self) -> None:
        self.state.selected_source_id = None
        self.state.manual_job_urls = ""
        self.state.source_form = UIState().source_form
        self.render_current_view()

    def save_source_form(self) -> None:
        payload = JobSourceConfig(
            id=self.state.source_form.get("id"),
            name=self.source_inputs["name"].value or "",
            source_type=self.source_inputs["source_type"].value or "auto",
            url=self.source_inputs["url"].value or "",
            identifier=self.source_inputs["identifier"].value or None,
            enabled=bool(self.source_inputs["enabled"].value),
            use_playwright=bool(self.source_inputs["use_playwright"].value),
            use_browser_profile=bool(self.source_inputs["use_browser_profile"].value),
            refresh_minutes=int(self.source_inputs["refresh_minutes"].value or 180),
            max_pages=max(1, int(self.source_inputs["max_pages"].value or DEFAULT_SOURCE_MAX_PAGES)),
            request_delay_ms=max(0, int(self.source_inputs["request_delay_ms"].value or DEFAULT_SOURCE_REQUEST_DELAY_MS)),
            notes=self.source_inputs["notes"].value or "",
        )
        if not payload.name or not payload.url:
            self._notify("Name and URL are required for a source.", type="warning")
            return
        try:
            saved = self.engine.save_source(payload)
        except Exception as exc:
            self._notify(str(exc), type="negative")
            return
        self.state.selected_source_id = saved.id
        self.state.source_form = {
            "id": saved.id,
            "name": saved.name,
            "source_type": saved.source_type,
            "url": saved.url,
            "identifier": saved.identifier or "",
            "enabled": saved.enabled,
            "use_playwright": saved.use_playwright,
            "use_browser_profile": saved.use_browser_profile,
            "refresh_minutes": saved.refresh_minutes,
            "max_pages": saved.max_pages,
            "request_delay_ms": saved.request_delay_ms,
            "notes": saved.notes,
        }
        self._notify(f"Saved source: {saved.name}", type="positive")
        self.render_sidebar()
        self.render_current_view()

    def delete_selected_source(self) -> None:
        if not self.state.selected_source_id:
            self._notify("Select a source first.", type="warning")
            return
        self.engine.delete_source(self.state.selected_source_id)
        self._notify("Source deleted.", type="positive")
        self.reset_source_form()
        self.render_sidebar()

    def save_settings(self) -> None:
        values = {key: element.value for key, element in self.settings_inputs.items()}
        self.engine.update_settings(values)
        self._apply_theme_mode(str(values.get("theme_mode", "auto")))
        if self.state.current_view == "settings":
            self.render_current_view()
        self._notify("Settings saved.", type="positive")

    def rotate_browser_api_token(self) -> None:
        token = self.engine.rotate_browser_api_token()
        if self.browser_token_input is not None:
            self.browser_token_input.value = token
            self.browser_token_input.update()
        self._notify("Rotated the browser capture token.", type="positive")

    async def handle_source_discovery(self) -> None:
        query = self.state.discovery_query.strip()
        if not query:
            self._notify("Enter a company name, homepage, or careers URL first.", type="warning")
            return
        self.status_label.set_text("Discovering sources...")
        self._append_activity(f"Discovering source candidates for {query}.")
        try:
            candidates = await asyncio.to_thread(self.engine.discover_sources, query)
            self.state.discovery_results = [self._candidate_to_dict(candidate) for candidate in candidates]
            if candidates:
                self._append_activity(f"Discovery found {len(candidates)} candidate source(s) for {query}.")
                self._notify(f"Found {len(candidates)} candidate source(s).", type="positive")
            else:
                self._append_activity(f"Discovery found no candidates for {query}.")
                self._notify("No candidates found from that query.", type="warning")
            self.render_current_view()
        except Exception as exc:
            self._append_activity(f"Source discovery failed for {query}: {exc}")
            self._notify(f"Source discovery failed: {exc}", type="negative")
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")
            self.render_scan_log_panel()

    def load_discovered_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self.state.discovery_results):
            return
        candidate = self.state.discovery_results[index]
        payload = self.engine.source_from_candidate(self._dict_to_candidate(candidate))
        self.state.selected_source_id = None
        self.state.source_form = {
            "id": None,
            "name": payload.name,
            "source_type": payload.source_type,
            "url": payload.url,
            "identifier": payload.identifier or "",
            "enabled": payload.enabled,
            "use_playwright": payload.use_playwright,
            "use_browser_profile": payload.use_browser_profile,
            "refresh_minutes": payload.refresh_minutes,
            "max_pages": payload.max_pages,
            "request_delay_ms": payload.request_delay_ms,
            "notes": payload.notes,
        }
        self.render_current_view()

    def save_discovered_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self.state.discovery_results):
            return
        candidate = self.state.discovery_results[index]
        payload = self.engine.source_from_candidate(self._dict_to_candidate(candidate))
        try:
            saved = self.engine.save_source(payload)
        except Exception as exc:
            self._notify(str(exc), type="negative")
            return
        self.state.selected_source_id = saved.id
        self.state.source_form = {
            "id": saved.id,
            "name": saved.name,
            "source_type": saved.source_type,
            "url": saved.url,
            "identifier": saved.identifier or "",
            "enabled": saved.enabled,
            "use_playwright": saved.use_playwright,
            "use_browser_profile": saved.use_browser_profile,
            "refresh_minutes": saved.refresh_minutes,
            "max_pages": saved.max_pages,
            "request_delay_ms": saved.request_delay_ms,
            "notes": saved.notes,
        }
        self._append_activity(f"Saved discovered source {saved.name} ({saved.url}).")
        self.render_sidebar()
        self.render_current_view()
        self._notify(f"Saved source: {saved.name}", type="positive")

    def open_selected_source_browser(self) -> None:
        source = self._selected_source()
        if source is None:
            self._notify("Select a source first.", type="warning")
            return
        browser_name = self.engine.open_source_in_browser_profile(source.id or 0)
        self._append_activity(f"Opened {source.name} in the dedicated {browser_name} profile.")
        self.render_scan_log_panel()
        self._notify(f"Opened in {browser_name}.", type="positive")

    def handle_export(self, export_format: str) -> None:
        if not self.state.matches:
            self._notify("Nothing to export yet.", type="warning")
            return
        path = self.engine.export_matches(export_format, self.state.matches)
        ui.download(path)

    def clear_filters(self) -> None:
        self.state.location_query = ""
        self.state.remote_mode = "any"
        self.state.job_type = "any"
        self.state.clearance_terms = []
        asyncio.create_task(self.refresh_matches())

    def current_filters(self) -> FilterCriteria:
        return FilterCriteria(
            location_query=self.state.location_query,
            remote_mode=self.state.remote_mode,
            job_type=self.state.job_type,
            clearance_terms=self.state.clearance_terms,
        )

    async def handle_manual_source_import(self) -> None:
        source = self._selected_source()
        if source is None:
            self._notify("Select a source first.", type="warning")
            return
        self.status_label.set_text("Importing source page...")
        self._append_activity(f"Manual import started for {source.name} from the saved source page.")
        try:
            result = await self.engine.import_source_page(source.id or 0)
            self.state.recent_scans = self.engine.list_recent_scans(limit=8)
            self._append_activity(
                f"Manual import finished for {source.name}: {result.jobs_created} new, {result.jobs_updated} updated, {result.jobs_unchanged} unchanged."
            )
            await self.refresh_matches(record_activity=False)
            self._notify(f"Imported {len(result.jobs)} jobs from the source page.", type="positive")
            self.render_sidebar()
            self.render_current_view()
        except Exception as exc:
            self._append_activity(f"Manual source import failed for {source.name}: {exc}")
            self._notify(f"Manual source import failed: {exc}", type="negative")
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")
            self.render_scan_log_panel()

    async def handle_source_html_upload(self, event: events.UploadEventArguments) -> None:
        source = self._selected_source()
        if source is None:
            self._notify("Select a source first.", type="warning")
            return
        incoming_path = UPLOADS_DIR / f"manual-source-{source.id or 0}-{safe_filename(Path(event.file.name).stem, '.html')}"
        await event.file.save(incoming_path)
        html_text = incoming_path.read_text(encoding="utf-8", errors="ignore")
        self.status_label.set_text("Importing saved HTML...")
        self._append_activity(f"Manual HTML import started for {source.name} from {event.file.name}.")
        try:
            result = await self.engine.import_saved_html(source.id or 0, html_text)
            self.state.recent_scans = self.engine.list_recent_scans(limit=8)
            self._append_activity(
                f"Saved HTML import finished for {source.name}: {result.jobs_created} new, {result.jobs_updated} updated, {result.jobs_unchanged} unchanged."
            )
            await self.refresh_matches(record_activity=False)
            self._notify(f"Imported {len(result.jobs)} jobs from saved HTML.", type="positive")
            self.render_sidebar()
            self.render_current_view()
        except Exception as exc:
            self._append_activity(f"Saved HTML import failed for {source.name}: {exc}")
            self._notify(f"Saved HTML import failed: {exc}", type="negative")
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")
            self.render_scan_log_panel()

    async def handle_job_url_import(self) -> None:
        source = self._selected_source()
        if source is None:
            self._notify("Select a source first.", type="warning")
            return
        urls = [line.strip() for line in self.state.manual_job_urls.splitlines() if line.strip()]
        if not urls:
            self._notify("Paste at least one job URL.", type="warning")
            return
        self.status_label.set_text("Importing job URLs...")
        self._append_activity(f"Manual URL import started for {source.name}: {len(urls)} URL(s).")
        try:
            result = await self.engine.import_job_urls(source.id or 0, urls)
            self.state.recent_scans = self.engine.list_recent_scans(limit=8)
            self._append_activity(
                f"URL import finished for {source.name}: {result.jobs_created} new, {result.jobs_updated} updated, {result.jobs_unchanged} unchanged."
            )
            await self.refresh_matches(record_activity=False)
            self._notify(f"Imported {len(result.jobs)} jobs from pasted URLs.", type="positive")
            self.state.manual_job_urls = ""
            self.render_sidebar()
            self.render_current_view()
        except Exception as exc:
            self._append_activity(f"URL import failed for {source.name}: {exc}")
            self._notify(f"URL import failed: {exc}", type="negative")
        finally:
            self.status_label.set_text(self.state.scan_status if self.state.scan_running else "Ready")
            self.render_scan_log_panel()

    def _selected_source(self):
        if not self.state.selected_source_id:
            return None
        return self.engine.get_source(self.state.selected_source_id)

    @staticmethod
    def _candidate_to_dict(candidate: DiscoveredSourceCandidate) -> dict[str, Any]:
        return {
            "name": candidate.name,
            "source_type": candidate.source_type,
            "url": candidate.url,
            "platform": candidate.platform,
            "reason": candidate.reason,
            "identifier": candidate.identifier,
            "use_playwright": candidate.use_playwright,
            "use_browser_profile": candidate.use_browser_profile,
        }

    @staticmethod
    def _dict_to_candidate(payload: dict[str, Any]) -> DiscoveredSourceCandidate:
        return DiscoveredSourceCandidate(
            name=str(payload.get("name") or ""),
            source_type=str(payload.get("source_type") or "custom_url"),
            url=str(payload.get("url") or ""),
            platform=str(payload.get("platform") or "careers page"),
            reason=str(payload.get("reason") or ""),
            identifier=payload.get("identifier") or None,
            use_playwright=bool(payload.get("use_playwright")),
            use_browser_profile=bool(payload.get("use_browser_profile")),
        )

    def _append_activity(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.state.scan_log_lines.append(line)
        self.state.scan_log_lines = self.state.scan_log_lines[-160:]
        if not self._client_deleted and self.scan_log_widget is not None:
            self.scan_log_widget.push(line, classes="mono text-xs")

    def _handle_scan_progress(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event") or "")
        if kind == "scan_started":
            sources = list(event.get("sources") or [])
            self.state.scan_source_total = int(event.get("source_count") or len(sources))
            self.state.scan_sources_finished = 0
            self.state.scan_status = f"Scanning 0/{self.state.scan_source_total} sources..."
            self.state.scan_rows = [
                self._new_scan_row(source.get("id"), source.get("name") or "Unnamed source")
                for source in sources
            ]
            self._append_activity(f"Scan queued for {self.state.scan_source_total} source(s).")
        elif kind == "source_started":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["status"] = "running"
            row["note"] = f"Opening {event.get('source_type') or 'source'} feed"
            self._append_activity(f"Scanning {row['source_name']}...")
        elif kind == "source_page":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["pages_scanned"] = int(event.get("page") or row["pages_scanned"])
            row["jobs_found"] = int(event.get("total_jobs") or row["jobs_found"])
            row["note"] = (
                f"Page {event.get('page')}: kept {event.get('jobs_kept', 0)}, "
                f"{event.get('new_or_changed_jobs', 0)} new/changed, {event.get('known_jobs', 0)} known"
            )
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_detail":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["detail_pages_fetched"] += int(event.get("detail_pages") or 0)
            row["note"] = f"Fetching {event.get('detail_pages', 0)} detail page(s) from page {event.get('page', '?')}"
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_fallback":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["note"] = f"Switching to browser rendering on page {event.get('page', '?')} after {event.get('reason', 'request block')}"
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_browser_session":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["note"] = "Using the persistent browser profile for this source scan"
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_browser_assist":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["note"] = (
                f"Waiting up to {event.get('wait_seconds', 0)}s for security-check clearance "
                "in the persistent browser profile window"
            )
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_early_stop":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["stopped_early"] = True
            row["note"] = f"Stopped early after page {event.get('page', '?')} because pages were mostly already known"
            self._append_activity(f"{row['source_name']}: {row['note']}.")
        elif kind == "source_finished":
            row = self._touch_scan_row(event.get("source_id"), event.get("source_name"))
            row["status"] = str(event.get("status") or "unknown")
            row["pages_scanned"] = int(event.get("pages_scanned") or row["pages_scanned"])
            row["jobs_found"] = int(event.get("jobs_found") or row["jobs_found"])
            row["jobs_created"] = int(event.get("jobs_created") or 0)
            row["jobs_updated"] = int(event.get("jobs_updated") or 0)
            row["jobs_unchanged"] = int(event.get("jobs_unchanged") or 0)
            row["jobs_deactivated"] = int(event.get("jobs_deactivated") or 0)
            row["detail_pages_fetched"] = int(event.get("detail_pages_fetched") or row["detail_pages_fetched"])
            row["stopped_early"] = bool(event.get("stopped_early") or row["stopped_early"])
            row["error"] = str(event.get("error") or "")
            row["block_reason"] = str(event.get("block_reason") or "")
            row["note"] = self._source_finished_note(row)
            self.state.scan_sources_finished = sum(
                1
                for scan_row in self.state.scan_rows
                if scan_row["status"] in {"ok", "not_modified", "error", "blocked", "cancelled"}
            )
            if self.state.scan_stop_requested:
                self.state.scan_status = (
                    f"Stopping scan... {self.state.scan_sources_finished}/{max(self.state.scan_source_total, 1)} sources settled"
                )
            else:
                self.state.scan_status = f"Scanning {self.state.scan_sources_finished}/{max(self.state.scan_source_total, 1)} sources..."
            if row["status"] == "blocked":
                self._append_activity(f"{row['source_name']}: blocked by site security checks.")
            elif row["status"] == "cancelled":
                self._append_activity(f"{row['source_name']}: cancelled before completion.")
            else:
                self._append_activity(
                    f"{row['source_name']}: {row['status']} ({row['jobs_created']} new, {row['jobs_updated']} updated, {row['jobs_unchanged']} unchanged)."
                )
        elif kind == "scan_cancelled":
            self.state.scan_status = (
                f"Scan stopped: {event.get('total_created', 0)} new, "
                f"{event.get('total_updated', 0)} updated, {event.get('cancelled_count', 0)} cancelled"
            )
            self._append_activity(
                f"Scan stopped: {event.get('total_jobs', 0)} job(s) kept, "
                f"{event.get('cancelled_count', 0)} source(s) cancelled before completion."
            )
        elif kind == "scan_finished":
            self.state.scan_status = (
                f"Scan complete: {event.get('total_created', 0)} new, "
                f"{event.get('total_updated', 0)} updated, {event.get('blocked_count', 0)} blocked, {event.get('error_count', 0)} issue(s)"
            )
            self._append_activity(
                f"Scan complete: {event.get('total_jobs', 0)} job(s), "
                f"{event.get('total_created', 0)} new, {event.get('total_updated', 0)} updated, {event.get('blocked_count', 0)} blocked."
            )

        if self.status_label is not None:
            self.status_label.set_text(self.state.scan_status)
        self.render_scan_summary_panel()
        self.render_scan_log_panel()

    def _apply_scan_summary(self, summary: ScanSummary) -> None:
        self.state.scan_source_total = len(summary.results)
        self.state.scan_sources_finished = len(summary.results)
        self.state.scan_rows = [self._scan_result_row(result) for result in summary.results]

    def _scan_complete_status(self, summary: ScanSummary) -> str:
        if summary.cancelled_count:
            return (
                f"Scan stopped: {summary.total_created} new, {summary.total_updated} updated, "
                f"{summary.cancelled_count} cancelled"
            )
        return (
            f"Scan complete: {summary.total_created} new, {summary.total_updated} updated, "
            f"{summary.blocked_count} blocked, {summary.error_count} issue(s)"
        )

    def _scan_totals(self) -> dict[str, int]:
        return {
            "created": sum(int(row["jobs_created"]) for row in self.state.scan_rows),
            "updated": sum(int(row["jobs_updated"]) for row in self.state.scan_rows),
            "unchanged": sum(int(row["jobs_unchanged"]) for row in self.state.scan_rows),
            "issues": sum(1 for row in self.state.scan_rows if row["status"] in {"error", "blocked"}),
        }

    def _touch_scan_row(self, source_id: Any, source_name: Any) -> dict[str, Any]:
        for row in self.state.scan_rows:
            if row["source_id"] == source_id and source_id is not None:
                if source_name:
                    row["source_name"] = str(source_name)
                return row
            if source_id is None and row["source_name"] == str(source_name or row["source_name"]):
                return row
        row = self._new_scan_row(source_id, str(source_name or "Unnamed source"))
        self.state.scan_rows.append(row)
        return row

    @staticmethod
    def _new_scan_row(source_id: Any, source_name: str) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "source_name": source_name,
            "status": "queued",
            "pages_scanned": 0,
            "jobs_found": 0,
            "jobs_created": 0,
            "jobs_updated": 0,
            "jobs_unchanged": 0,
            "jobs_deactivated": 0,
            "detail_pages_fetched": 0,
            "stopped_early": False,
            "note": "Waiting to start",
            "error": "",
            "block_reason": "",
        }

    @staticmethod
    def _source_finished_note(row: dict[str, Any]) -> str:
        if row["status"] == "blocked" and row["error"]:
            return row["error"]
        if row["status"] == "cancelled":
            return row["error"] or "Cancelled before completion."
        if row["status"] == "error" and row["error"]:
            return row["error"]
        note = (
            f"{row['jobs_found']} job(s) across {row['pages_scanned']} page(s); "
            f"{row['detail_pages_fetched']} detail page(s)"
        )
        if row["stopped_early"]:
            note += "; stopped early after mostly-known pages"
        return note

    def _scan_result_row(self, result) -> dict[str, Any]:
        return {
            "source_id": result.source.id,
            "source_name": result.source.name,
            "status": result.status,
            "pages_scanned": result.pages_scanned,
            "jobs_found": len(result.jobs),
            "jobs_created": result.jobs_created,
            "jobs_updated": result.jobs_updated,
            "jobs_unchanged": result.jobs_unchanged,
            "jobs_deactivated": result.jobs_deactivated,
            "detail_pages_fetched": result.detail_pages_fetched,
            "stopped_early": result.stopped_early,
            "block_reason": result.block_reason or "",
            "note": result.error or self._source_finished_note(
                {
                    "status": result.status,
                    "error": result.error or "",
                    "jobs_found": len(result.jobs),
                    "pages_scanned": result.pages_scanned,
                    "detail_pages_fetched": result.detail_pages_fetched,
                    "stopped_early": result.stopped_early,
                    "block_reason": result.block_reason or "",
                }
            ),
            "error": result.error or "",
        }

    @staticmethod
    def _scan_row_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": row["source_name"],
            "status": str(row["status"]).replace("_", " "),
            "pages": row["pages_scanned"] or "-",
            "jobs": row["jobs_found"],
            "changes": f"+{row['jobs_created']} / ~{row['jobs_updated']} / ={row['jobs_unchanged']}",
            "note": row["note"],
        }

    @staticmethod
    def _recent_scan_row(scan: dict[str, Any]) -> dict[str, Any]:
        finished = scan.get("finished_at")
        finished_text = finished.strftime("%m-%d %H:%M") if finished else "running"
        return {
            "id": scan["id"],
            "source": scan.get("source_name") or f"Source #{scan.get('source_id')}",
            "status": str(scan.get("status") or "unknown").replace("_", " "),
            "finished": finished_text,
            "changes": (
                f"+{scan.get('jobs_created', 0)} / ~{scan.get('jobs_updated', 0)} / ={scan.get('jobs_unchanged', 0)}"
            ),
        }

    def _render_results_table(self, matches: list[MatchResult]) -> None:
        rows = [self._match_row(match) for match in matches]
        columns = [
            {"name": "expander", "label": "", "field": "id"},
            {"name": "score_value", "label": "Match", "field": "score_value", "sortable": True},
            {"name": "title", "label": "Role", "field": "title", "sortable": True},
            {"name": "job_type", "label": "Type", "field": "job_type", "sortable": True},
            {"name": "salary_text", "label": "Salary", "field": "salary_text", "sortable": True},
            {"name": "open_action", "label": "", "field": "open_action"},
        ]
        table = ui.table(rows=rows, columns=columns, row_key="id", pagination={"rowsPerPage": 18, "sortBy": "score_value", "descending": True}).classes("w-full match-table")
        table.props("flat square separator=horizontal")
        table.add_slot(
            "body",
            r"""
            <q-tr :props="props">
              <q-td auto-width>
                <q-btn flat dense round size="sm"
                  :icon="props.expand ? 'keyboard_arrow_down' : 'keyboard_arrow_right'"
                  @click="props.expand = !props.expand" />
              </q-td>
              <q-td key="score_value" :props="props">
                <span class="score-pill">{{ props.row.score_display }}</span>
              </q-td>
              <q-td key="title" :props="props">
                <div class="job-primary">{{ props.row.title }}</div>
                <div class="job-secondary">{{ props.row.matched_summary }}</div>
                <div class="match-role-meta">
                  <span>{{ props.row.company }}</span>
                  <span>{{ props.row.location }}</span>
                  <span>{{ props.row.remote_mode }}</span>
                </div>
              </q-td>
              <q-td key="job_type" :props="props">{{ props.row.job_type }}</q-td>
              <q-td key="salary_text" :props="props">
                <span v-if="props.row.salary_text" class="salary-pill">{{ props.row.salary_text }}</span>
                <span v-else class="job-secondary">-</span>
              </q-td>
              <q-td key="open_action" :props="props" auto-width>
                <q-btn
                  color="primary"
                  dense
                  unelevated
                  no-caps
                  icon-right="open_in_new"
                  label="Open"
                  :href="props.row.url"
                  target="_blank"
                />
              </q-td>
            </q-tr>
            <q-tr v-show="props.expand" :props="props">
              <q-td colspan="100%">
                <div class="detail-grid">
                  <div class="detail-block">
                    <div class="detail-title">Why It Scored This Way</div>
                    <div class="detail-copy">{{ props.row.reasons_text }}</div>
                  </div>
                  <div class="detail-block">
                    <div class="detail-title">Matched Skills</div>
                    <div class="detail-copy">{{ props.row.matched_skills }}</div>
                    <div class="detail-title" style="margin-top: 0.8rem;">Missing Skills</div>
                    <div class="detail-copy">{{ props.row.missing_skills }}</div>
                  </div>
                  <div class="detail-block">
                    <div class="detail-title">Posting Details</div>
                    <div class="detail-copy">Source: {{ props.row.source_name }}</div>
                    <div class="detail-copy">Type: {{ props.row.job_type || 'unspecified' }}</div>
                    <div class="detail-copy">Salary: {{ props.row.salary_text || 'Not provided' }}</div>
                    <div class="detail-copy">Clearance: {{ props.row.clearance }}</div>
                    <div class="detail-copy">Posted: {{ props.row.posted_at }}</div>
                    <div style="margin-top: 0.75rem;">
                      <q-btn
                        color="primary"
                        unelevated
                        no-caps
                        icon-right="open_in_new"
                        label="Open posting"
                        :href="props.row.url"
                        target="_blank"
                      />
                    </div>
                  </div>
                </div>
                <div class="detail-block" style="margin-top: 1rem;">
                  <div class="detail-title">Description</div>
                  <div class="detail-copy">{{ props.row.description }}</div>
                </div>
              </q-td>
            </q-tr>
            """,
        )

    @staticmethod
    def _match_row(match: MatchResult) -> dict[str, Any]:
        clearance = ", ".join(match.job.clearance_terms) if match.job.clearance_terms else "None"
        matched_skills = ", ".join(match.matched_skills) if match.matched_skills else "No direct matches extracted."
        missing_skills = ", ".join(match.missing_skills) if match.missing_skills else "No obvious gaps detected."
        reasons_text = "\n".join(match.reasons)
        matched_summary = f"{len(match.matched_skills)} matched skill(s) - {match.embedding_score * 100:.0f}% semantic fit"
        type_display, salary_display = JobMatchUI._display_type_and_salary(match.job.job_type, match.job.salary_text, match.job.employment_text)
        return {
            "id": match.job_id,
            "score_value": round(match.score, 4),
            "score_display": f"{match.score * 100:.0f}%",
            "title": match.job.title,
            "company": match.job.company,
            "location": match.job.location or "Unspecified",
            "remote_mode": match.job.remote_mode,
            "job_type": type_display,
            "salary_text": salary_display,
            "matched_summary": matched_summary,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "reasons_text": reasons_text,
            "clearance": clearance,
            "posted_at": match.job.posted_at.strftime("%Y-%m-%d") if match.job.posted_at else "Unknown",
            "url": match.job.url,
            "source_name": match.job.source_name,
            "description": clipped_excerpt(match.job.description, 1200),
        }

    @staticmethod
    def _display_type_and_salary(job_type: str | None, salary_text: str | None, employment_text: str | None) -> tuple[str, str]:
        parsed_salary = extract_salary_info(salary_text or "").get("display") if salary_text else None
        type_parts: list[str] = []
        seen: set[str] = set()
        for value in [job_type or "", employment_text or ""]:
            normalized = normalize_whitespace(value)
            if not normalized:
                continue
            folded = normalized.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            type_parts.append(normalized)
        if salary_text and not parsed_salary:
            normalized_salary = normalize_whitespace(salary_text)
            if normalized_salary and normalized_salary.casefold() not in seen:
                type_parts.append(normalized_salary)
        type_display = " | ".join(type_parts) if type_parts else "unspecified"
        return type_display, parsed_salary or ""

    @staticmethod
    def _stat_block(label: str, value: str) -> None:
        with ui.element("div").classes("stat-block"):
            ui.label(label).classes("section-label")
            ui.label(value).classes("stat-value")

    @staticmethod
    def _empty_state(message: str) -> None:
        with ui.element("div").classes("empty-state"):
            ui.label(message).classes("text-base")


@ui.page("/")
def main_page() -> None:
    JobMatchUI(ENGINE).mount()


def _resolve_port(port: int | None) -> int:
    if port is not None:
        return port
    env_port = os.getenv("JOBMATCH_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    return 8181


def _resolve_host(host: str | None) -> str:
    if host:
        return host
    env_host = os.getenv("JOBMATCH_HOST")
    if env_host:
        return env_host
    return "127.0.0.1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JobMatch NiceGUI app.")
    parser.add_argument("--host", type=str, default=None, help="Host interface to bind the local web app to.")
    parser.add_argument("--port", type=int, default=None, help="Port to bind the local web app to.")
    return parser.parse_args()


def run(port: int | None = None, host: str | None = None) -> None:
    ui.run(
        title=APP_NAME,
        dark=False,
        host=_resolve_host(host),
        reload=False,
        show_welcome_message=False,
        port=_resolve_port(port),
    )


if __name__ in {"__main__", "__mp_main__"}:
    args = _parse_args()
    run(port=args.port, host=args.host)
