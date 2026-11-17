"""Start screen: saved connections on the left, connection form on the right."""
from typing import Any, Callable

from nicegui import run, ui

from backend import service
from ui.components import error_box


def build(on_connected: Callable[[], None]) -> None:
    sections: dict[str, dict[str, Any]] = {}

    with ui.row().classes("w-full max-w-6xl mx-auto mt-6 px-4 gap-6 items-start no-wrap"):

        # ---------------- saved connections ----------------
        with ui.card().classes("w-72 shrink-0 p-4 gap-2"):
            ui.label("Saved connections").classes("text-base font-semibold")
            saved_box = ui.column().classes("w-full gap-1")

        # ---------------- connection form ------------------
        with ui.card().classes("flex-1 p-6 gap-4"):
            ui.label("Connect to database").classes("text-xl font-semibold")

            with ui.row().classes("w-full items-end gap-6 no-wrap"):
                alias = ui.input("Alias", placeholder="e.g. TESTDB14 UAT").classes("flex-1")
                with ui.column().classes("gap-0"):
                    ui.label("Environment").classes("text-xs text-gray-500")
                    env = ui.toggle(list(service.ENV_TYPES), value="UAT")

            banner = ui.column().classes("w-full")

            for role, label in service.SCHEMA_ROLES.items():
                with ui.expansion(label, icon="storage", value=(role == "fcubs")).classes(
                    "w-full border rounded"
                ) as exp:
                    with ui.grid(columns=6).classes("w-full gap-x-4 gap-y-1 p-2"):
                        host = ui.input("Host").classes("col-span-4")
                        port = ui.number("Port", value=1521, precision=0, min=1, max=65535).classes("col-span-2")
                        with ui.column().classes("col-span-2 gap-0 justify-end"):
                            ui.label("Connect by").classes("text-xs text-gray-500")
                            mode = ui.toggle({"sid": "SID", "service": "Service name"}, value="sid")
                        name = ui.input("SID / Service name").classes("col-span-4")
                        user = ui.input("User").classes("col-span-3")
                        pw = ui.input("Password", password=True, password_toggle_button=True).classes(
                            "col-span-3"
                        ).props("autocomplete=off")
                        owner = dblink = None
                        if role != "fcubs":
                            owner = ui.input("Table owner", value=service.DEFAULT_OWNERS.get(role, "")).classes(
                                "col-span-3"
                            )
                            dblink = ui.input("DB link from FLEXCUBE", placeholder="e.g. OBBUAT").classes("col-span-3")
                    if role != "fcubs":
                        ui.label(
                            "Optional. Either fill in a direct connection above, or leave it empty and give "
                            "the DB link to reach these tables through the FLEXCUBE connection."
                        ).classes("text-xs text-gray-500 px-2 pb-2")
                sections[role] = {"exp": exp, "host": host, "port": port, "mode": mode,
                                  "name": name, "user": user, "pw": pw, "owner": owner, "dblink": dblink}

            save_box = ui.checkbox("Save these details for next time (the password is never saved)", value=True)
            results = ui.column().classes("w-full gap-2")
            button = ui.button("Connect", icon="power").classes("w-full")

    # ---------------- behaviour -------------------------------------------------

    def render_banner() -> None:
        banner.clear()
        with banner:
            if env.value == "PROD":
                with ui.row().classes("w-full items-center gap-2 bg-red-700 text-white rounded px-3 py-2 no-wrap"):
                    ui.icon("lock")
                    ui.label("PRODUCTION - read only. INSERT, UPDATE and DELETE are blocked.").classes("font-semibold")
            else:
                try:
                    count = len(service.forbidden_tables())
                    text = f"UAT - changes allowed, except on {count} forbidden tables."
                except service.ServiceError as exc:
                    text = f"UAT - changes blocked: {exc}"
                with ui.row().classes("w-full items-center gap-2 bg-amber-100 text-amber-900 rounded px-3 py-2 no-wrap"):
                    ui.icon("edit_note")
                    ui.label(text)

    def collect() -> tuple[dict[str, Any], dict[str, str]]:
        schemas: dict[str, Any] = {}
        objects: dict[str, Any] = {}
        passwords: dict[str, str] = {}
        for role, f in sections.items():
            if f["owner"] is not None:
                objects[role] = {"owner": f["owner"].value, "dblink": f["dblink"].value}
            db_name = (f["name"].value or "").strip()
            schemas[role] = {
                "host": f["host"].value,
                "port": f["port"].value,
                "user": f["user"].value,
                "sid": db_name if f["mode"].value == "sid" else "",
                "service": db_name if f["mode"].value == "service" else "",
            }
            passwords[role] = f["pw"].value or ""
        return {"alias": alias.value, "env_type": env.value, "schemas": schemas, "objects": objects}, passwords

    def fill(profile: dict[str, Any]) -> None:
        alias.value = profile["alias"]
        env.value = profile["env_type"]
        for role, f in sections.items():
            cfg = profile["schemas"].get(role)
            f["host"].value = (cfg or {}).get("host") or ""
            f["port"].value = (cfg or {}).get("port") or 1521
            f["user"].value = (cfg or {}).get("user") or ""
            f["mode"].value = "service" if cfg and cfg.get("service") else "sid"
            f["name"].value = (cfg or {}).get("service") or (cfg or {}).get("sid") or ""
            f["pw"].value = ""
            obj = (profile.get("objects") or {}).get(role) or {}
            if f["owner"] is not None:
                f["owner"].value = obj.get("owner") or ""
                f["dblink"].value = obj.get("dblink") or ""
            f["exp"].value = cfg is not None or bool(obj.get("dblink"))
        results.clear()
        ui.notify(f"Loaded '{profile['alias']}'. Enter the password to connect.")

    def remove(profile_alias: str) -> None:
        try:
            service.delete_profile(profile_alias)
        except service.ServiceError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(f"Removed '{profile_alias}'")
        render_saved()

    def render_saved() -> None:
        saved_box.clear()
        with saved_box:
            try:
                saved = service.list_profiles()
            except service.ServiceError as exc:
                error_box(str(exc))
                return
            if not saved:
                ui.label("None yet. Connect once with 'Save' ticked.").classes("text-sm text-gray-500")
                return
            for p in saved:
                fcubs = p["schemas"].get("fcubs") or next(iter(p["schemas"].values()))
                prod = p["env_type"] == "PROD"
                with ui.row().classes(
                    "w-full items-center no-wrap gap-2 rounded px-2 py-1 hover:bg-gray-100 cursor-pointer"
                ) as row:
                    ui.badge(p["env_type"], color="red-7" if prod else "green-7")
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(p["alias"]).classes("font-medium truncate")
                        ui.label(f"{fcubs['user']} @ {service.describe_target(fcubs)}").classes(
                            "text-xs text-gray-500 truncate"
                        )
                    ui.button(icon="delete", on_click=lambda _, a=p["alias"]: remove(a)).props(
                        "flat dense round size=sm color=grey-7"
                    ).on("click.stop", lambda: None)
                row.on("click", lambda _, prof=p: fill(prof))

    async def connect() -> None:
        profile, passwords = collect()
        results.clear()
        try:
            clean = service.validate_profile(profile)
        except service.ServiceError as exc:
            with results:
                error_box(str(exc))
            return
        if not any(passwords.get(role) for role in clean["schemas"]):
            ui.notify("Enter the password", type="warning")
            return

        button.props("loading")
        button.disable()
        try:
            outcome = await run.io_bound(service.connect, profile, passwords, save_box.value)
        except service.ServiceError as exc:
            with results:
                error_box(str(exc))
            return
        finally:
            passwords.clear()
            button.props(remove="loading")
            button.enable()

        if any(r["ok"] for r in outcome.values()):
            for f in sections.values():
                f["pw"].value = ""
            on_connected()
            return
        with results:
            for role, res in outcome.items():
                if res["error"]:
                    error_box(f"{service.SCHEMA_ROLES[role]}: {res['error']}")
                elif res["skipped"]:
                    error_box(f"{service.SCHEMA_ROLES[role]}: skipped (no password)")

    button.on_click(connect)
    for f in sections.values():
        f["pw"].on("keydown.enter", connect)
    env.on_value_change(lambda _: render_banner())
    render_banner()
    render_saved()
