"""Entry point: starts the Support Console desktop window."""
from multiprocessing import freeze_support

from nicegui import native, ui

from backend import service
from ui import layout


def start() -> None:
    settings = service.load_app_settings()
    ui.run(
        layout.root,
        native=settings["native"],
        title=settings["title"],
        window_size=(settings["width"], settings["height"]) if settings["native"] else None,
        host="127.0.0.1",
        port=native.find_open_port(),   # random free port, not the well-known 8080
        reload=False,                   # required for packaging
        show=not settings["native"],
        show_welcome_message=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    freeze_support()
    start()
