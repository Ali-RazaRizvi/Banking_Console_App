"""Reusable UI pieces: error box, parameter inputs, query result card."""
import datetime as dt
from typing import Any

from nicegui import run, ui

from backend import service


def error_box(message: str) -> None:
    with ui.card().classes("w-full bg-red-50 border border-red-300 shadow-none p-3"):
        with ui.row().classes("items-start no-wrap gap-2"):
            ui.icon("error", color="red-8")
            ui.label(message).classes("text-red-900 whitespace-pre-line text-sm")


def param_input(p: dict[str, Any], on_enter) -> ui.input:
    label = p["label"] + ("" if p["required"] else " (optional)")
    if p["type"] == "date":
        with ui.input(label, placeholder=p["placeholder"] or "YYYY-MM-DD").props("dense clearable").classes("w-48") as inp:
            with ui.menu().props("no-parent-event") as menu:
                with ui.date().bind_value(inp):
                    with ui.row().classes("justify-end"):
                        ui.button("Close", on_click=menu.close).props("flat")
            with inp.add_slot("append"):
                ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
    else:
        inp = ui.input(label, placeholder=p["placeholder"]).props("dense clearable").classes("w-48")
    inp.on("keydown.enter", on_enter)
    return inp


class QueryCard:
    """One dashboard card: title, optional params, refresh, result table or count."""

    def __init__(self, q: dict[str, Any]) -> None:
        self.q = q
        self.inputs: dict[str, ui.input] = {}
        self.is_count = q["display"] == "count"
        self.busy = False

        with ui.card().classes("w-64 p-4 gap-1" if self.is_count else "w-full p-4 gap-2"):
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.label(q["title"]).classes("font-semibold " + ("text-sm text-gray-600" if self.is_count else "text-base"))
                ui.space()
                self.spinner = ui.spinner(size="1.2em")
                self.spinner.visible = False
                self.button = ui.button(icon="refresh", on_click=self.refresh).props("flat dense round")
                self.button.tooltip("Refresh")

            if q.get("description") and not self.is_count:
                ui.label(q["description"]).classes("text-xs text-gray-500")

            if q["params"]:
                with ui.row().classes("w-full items-end gap-3"):
                    for p in q["params"]:
                        self.inputs[p["name"]] = param_input(p, on_enter=self.refresh)
                    ui.button("Run", icon="play_arrow", on_click=self.refresh).props("unelevated dense").classes("px-3")

            self.error = ui.column().classes("w-full")
            self.result = ui.column().classes("w-full gap-1")
            self.meta = ui.label("").classes("text-xs text-gray-500")

        if not q["available"]:
            self.button.disable()
            with self.result:
                with ui.row().classes("items-center gap-2 text-gray-500"):
                    ui.icon("link_off")
                    ui.label(q["unavailable_reason"] or "Not available").classes("text-sm")
        elif self.is_count:
            with self.result:
                ui.label("-").classes("text-4xl font-bold text-gray-300")

    def ready(self) -> bool:
        """Can run without the user typing anything (available, no required params)."""
        return self.q["available"] and not any(p["required"] for p in self.q["params"])

    async def refresh(self) -> None:
        if not self.q["available"] or self.busy:
            return
        params = {name: inp.value for name, inp in self.inputs.items()}
        for p in self.q["params"]:
            if p["required"] and not (params.get(p["name"]) or "").strip():
                ui.notify(f"Enter '{p['label']}'", type="warning")
                return

        self.busy = True
        self.spinner.visible = True
        self.button.disable()
        self.error.clear()
        try:
            res = await run.io_bound(service.run_query, self.q["id"], params)
        except service.ServiceError as exc:
            with self.error:
                error_box(str(exc))
            self.meta.set_text(f"Failed at {dt.datetime.now():%H:%M:%S}")
            return
        finally:
            self.busy = False
            self.spinner.visible = False
            self.button.enable()
        self._render(res)

    def _render(self, res: dict[str, Any]) -> None:
        self.result.clear()
        now = f"{dt.datetime.now():%H:%M:%S}"
        with self.result:
            if self.is_count:
                value = res["rows"][0][0] if res["rows"] else 0
                ui.label(f"{value:,}" if isinstance(value, int) else str(value)).classes(
                    "text-4xl font-bold " + ("text-amber-700" if value else "text-green-700")
                )
                self.meta.set_text(f"Refreshed {now}")
                return

            if not res["rows"]:
                ui.label("No rows").classes("text-gray-500 text-sm")
                self.meta.set_text(f"Refreshed {now}")
                return

            columns = [
                {"name": f"c{i}", "label": c, "field": f"c{i}", "align": "left", "sortable": True}
                for i, c in enumerate(res["columns"])
            ]
            rows = [{"_i": n, **{f"c{i}": v for i, v in enumerate(r)}} for n, r in enumerate(res["rows"])]
            search = ui.input(placeholder="Search in results").props("dense clearable outlined").classes("w-72")
            table = ui.table(columns=columns, rows=rows, row_key="_i", pagination={"rowsPerPage": 15}).props(
                "dense flat bordered"
            ).classes("w-full")
            search.bind_value(table, "filter")

        more = f" - showing the first {res['row_count']}" if res["truncated"] else ""
        self.meta.set_text(f"{res['row_count']} rows{more} - refreshed {now}")
