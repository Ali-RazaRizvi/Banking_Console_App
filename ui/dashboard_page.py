"""Dashboard tab: cards built from config/queries/*.yaml (page: dashboard)."""
import asyncio

from nicegui import run, ui

from backend import service
from ui.components import QueryCard, error_box

_PARALLEL = 3          # cards refreshed at the same time by "Refresh all"


def build() -> None:
    container = ui.column().classes("w-full gap-4")
    cards: list[QueryCard] = []

    async def refresh_all() -> None:
        sem = asyncio.Semaphore(_PARALLEL)

        async def one(card: QueryCard) -> None:
            async with sem:
                await card.refresh()

        await asyncio.gather(*(one(c) for c in cards if c.ready()))

    async def reload_queries() -> None:
        try:
            n = await run.io_bound(service.reload_registry)
            ui.notify(f"Loaded {n} queries", type="positive")
        except service.ServiceError as exc:
            ui.notify(str(exc), type="negative", multi_line=True)
        render()

    def render() -> None:
        container.clear()
        cards.clear()
        with container:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Dashboard").classes("text-xl font-semibold")
                ui.space()
                ui.button("Refresh all", icon="refresh", on_click=refresh_all)
                ui.button("Reload queries", icon="sync", on_click=reload_queries).props("flat")

            try:
                queries = service.list_queries("dashboard")
            except service.ServiceError as exc:
                error_box(f"Could not load the query files:\n{exc}")
                return
            if not queries:
                ui.label("No dashboard queries yet. Add them in config/queries/.").classes("text-gray-500")
                return

            groups: dict[str, list[dict]] = {}
            for q in queries:
                groups.setdefault(q["group"], []).append(q)

            for group, items in groups.items():
                ui.label(group).classes("text-lg font-semibold mt-2")
                counts = [q for q in items if q["display"] == "count"]
                tables = [q for q in items if q["display"] != "count"]
                if counts:
                    with ui.row().classes("w-full gap-4"):
                        cards.extend(QueryCard(q) for q in counts)
                for q in tables:
                    cards.append(QueryCard(q))

            ui.timer(0.1, refresh_all, once=True)    # load once when the tab opens

    render()
