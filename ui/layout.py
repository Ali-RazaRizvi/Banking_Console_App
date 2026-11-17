"""Page layout: header (alias, UAT/PROD, schema dots, disconnect) and main area."""
from nicegui import run, ui

from backend import service
from ui import connect_page, dashboard_page
from ui.components import error_box


def _connection_info(st: dict) -> None:
    with ui.column().classes("w-full max-w-2xl gap-3"):
        ui.label(f"Connected to {st['alias']} ({st['env_type']})").classes("text-xl font-semibold")
        for name, ok in st["schemas"].items():
            label = service.SCHEMA_ROLES.get(name, name)
            error = st["errors"].get(name)
            if ok:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="green-7")
                    ui.label(f"{label}: connected")
            elif error:
                error_box(f"{label}: {error}")
            else:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("remove_circle_outline", color="grey-6")
                    ui.label(f"{label}: not connected (no password given)").classes("text-gray-600")
        for role, link in st["links"].items():
            with ui.row().classes("items-center gap-2"):
                ui.icon("link", color="blue-7")
                ui.label(f"{service.SCHEMA_ROLES.get(role, role)}: through the FLEXCUBE connection, DB link @{link}")

        ui.separator().classes("my-2")
        if st["is_production"]:
            with ui.row().classes("items-center gap-2 text-red-800"):
                ui.icon("lock")
                ui.label("Read only. INSERT, UPDATE and DELETE are blocked by the app and by the database session.")
        else:
            try:
                tables = service.forbidden_tables()
                with ui.row().classes("items-center gap-2 text-amber-900"):
                    ui.icon("edit_note")
                    ui.label(f"Changes allowed, except on {len(tables)} forbidden tables.")
                with ui.expansion("Show forbidden tables").classes("w-full text-sm"):
                    ui.label(", ".join(tables) or "(none)").classes("text-gray-700")
            except service.ServiceError as exc:
                error_box(f"Changes are blocked: {exc}")


def root() -> None:
    """Builds the single page of the app (passed to ui.run as root)."""
    settings = service.load_app_settings()
    header = ui.header().classes("items-center gap-4 px-4 py-2")

    def refresh_all() -> None:
        header_content.refresh()
        body.refresh()

    async def do_disconnect() -> None:
        await run.io_bound(service.disconnect)
        ui.notify("Disconnected")
        refresh_all()

    @ui.refreshable
    def header_content() -> None:
        st = service.status()
        connected = st["alias"] is not None
        production = connected and st["is_production"]
        header.classes(remove="bg-red-700 bg-slate-800", add="bg-red-700" if production else "bg-slate-800")

        ui.label(settings["title"]).classes("text-lg font-semibold")
        if not connected:
            ui.badge("NOT CONNECTED", color="grey-7")
        else:
            ui.label(st["alias"]).classes("text-base")
            if production:
                ui.badge("PRODUCTION - READ ONLY", color="white", text_color="red-9").classes("text-sm font-bold")
            else:
                ui.badge("UAT", color="green-7").classes("text-sm")

        for name, ok in st["schemas"].items():
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("circle", color="green-5" if ok else "grey-5", size="xs")
                ui.label(name).classes("text-sm")
        for role, link in st["links"].items():
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.icon("link", color="light-blue-3", size="xs")
                ui.label(f"{role} @{link}").classes("text-sm")

        ui.space()
        if connected:
            ui.button("Disconnect", icon="power_off", on_click=do_disconnect).props("flat color=white")

    @ui.refreshable
    def body() -> None:
        st = service.status()
        if st["alias"] is None:
            connect_page.build(on_connected=refresh_all)
            return

        with ui.tabs().classes("w-full bg-white text-slate-800 shadow-sm").props("align=left") as tabs:
            dash_tab = ui.tab("Dashboard", icon="dashboard")
            conn_tab = ui.tab("Connection", icon="lan")
        with ui.tab_panels(tabs, value=dash_tab).classes("w-full bg-transparent"):
            with ui.tab_panel(dash_tab).classes("px-4"):
                dashboard_page.build()
            with ui.tab_panel(conn_tab).classes("px-4"):
                _connection_info(st)

    with header:
        header_content()
    body()
