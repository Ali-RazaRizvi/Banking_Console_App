"""Page layout: header and main area."""
from nicegui import ui

from backend import service


def root() -> None:
    """Builds the single page of the app (passed to ui.run as root)."""
    settings = service.load_app_settings()

    with ui.header().classes("items-center bg-slate-800 text-white"):
        ui.label(settings["title"]).classes("text-lg font-semibold")
        ui.badge("NOT CONNECTED", color="grey").classes("ml-4")
        ui.space()

    with ui.column().classes("w-full items-center p-8"):
        ui.label("Connect screen goes here (phase 4).").classes("text-gray-500")
