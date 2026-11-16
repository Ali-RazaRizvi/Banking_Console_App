# Build Spec: Banking Support Console (Desktop App)

Give this whole file to the AI assistant in VS Code and ask it to build the project phase by phase (see section 11). Do not skip the rules in section 2.

---

## 1. Goal

A small desktop application for support staff working on Oracle FLEXCUBE / OBBRN / OBPM databases.

- It opens in its own window like any desktop program. No browser tab, no VS Code needed to run it.
- The user picks an environment (UAT or PROD), enters DB passwords, and connects.
- A dashboard shows branch dates, open teller batches, open vault batches, pending transactions and transactions awaiting approval.
- An actions page lets the user unlock a user or reset a password, and switch DB debug logs, OBPM logs and OBBRN logs on or off, for the whole system or for one user.
- Every dashboard card and every action is defined by a block in one YAML file. Adding a feature later means adding a block, not writing code.

## 2. Hard rules (non-negotiable)

1. **Python only.** Python 3.10+ is installed on the machine. The UI is written in Python with NiceGUI. No hand-written HTML or JavaScript, no Node.
2. **Backend and UI are separate.** Everything in `backend/` must work without NiceGUI (it can be imported and called from a plain Python script). The `ui/` package only calls backend functions. This keeps the option of changing the UI later.
3. **No internet at runtime.** Do not load anything from a CDN.
4. **All SQL lives in `config/queries.yaml`.** No SQL strings inside Python files.
5. **Bind variables only** (`:user_id`). Never build SQL with string formatting or concatenation.
6. **Passwords are never written to disk.** They are typed in the app at connect time and held in memory until the app closes.
7. **Write actions are separated from read queries** by `type: write` in the YAML, and follow the safety rules in section 7. The backend enforces them, not the UI.
8. **Every write action is logged** to `logs/audit.log` (section 8).
9. **The SQL text is never shown in the UI.** The UI shows titles, parameters and results only.
10. **Database calls must not freeze the window.** Run every backend call through `await run.io_bound(...)`.

## 3. Tech stack

| Part | Choice | Reason |
|---|---|---|
| UI | NiceGUI in native mode | UI written in Python, opens as a desktop window, ships its own assets so it works offline |
| Window | pywebview (used by NiceGUI native mode) | Uses the Edge WebView2 runtime already present on Windows 10 and 11 |
| DB driver | `oracledb` in thin mode | No Oracle Client install needed |
| Config | PyYAML | Human-editable query registry |
| Packaging | PyInstaller through `nicegui-pack` | Produces one `.exe` to double-click |

`requirements.txt`:

```
nicegui
pywebview
oracledb
pyyaml
pyinstaller
```

Notes for the AI assistant:

- The NiceGUI snippets in this file are written from memory. Check them against the installed NiceGUI version's documentation before using them.
- Thin mode needs Oracle Database 12.1 or later. If connecting fails with a password verifier error (DPY-3015), support an optional `oracle_client_dir` setting in `connections.yaml` and call `oracledb.init_oracle_client(lib_dir=...)` when it is set.
- If the native window fails to open on a machine (missing WebView2), `native` can be set to `false` in `config/app.yaml` and the same app opens in the default browser instead.

## 4. Folder structure

```
support-console/
├── main.py                entry point, starts the desktop window
├── requirements.txt
├── config/
│   ├── app.yaml           window title, size, native true/false
│   ├── connections.yaml   environments and schemas (no passwords)
│   └── queries.yaml       ALL dashboard cards and actions
├── backend/               no NiceGUI imports allowed here
│   ├── paths.py           finds config/ and logs/ (works when packaged)
│   ├── db.py              connection manager (one pool per schema)
│   ├── registry.py        loads and validates queries.yaml
│   ├── runner.py          runs a registry entry with bind params
│   ├── audit.py           audit log writer and reader
│   └── service.py         the only module the UI calls (section 9)
├── ui/
│   ├── layout.py          header, tabs, production styling
│   ├── connect_page.py
│   ├── dashboard_page.py
│   ├── actions_page.py
│   ├── audit_page.py
│   └── components.py      result table, confirm dialog, error box
└── logs/
    └── audit.log
```

## 5. Running and packaging

### 5.1 First-time install

```
pip install -r requirements.txt
```

If the machine has no internet, run `pip download -r requirements.txt -d wheels` on a machine that has it, copy the `wheels` folder across, then run `pip install --no-index --find-links=wheels -r requirements.txt`.

### 5.2 Run during development

```
python main.py
```

### 5.3 `main.py` outline

```python
from multiprocessing import freeze_support
from nicegui import ui
from backend import service
from ui import layout

def start():
    settings = service.load_app_settings()      # reads config/app.yaml
    layout.build()                               # registers the page
    ui.run(
        native=settings["native"],
        title=settings["title"],
        window_size=(settings["width"], settings["height"]),
        host="127.0.0.1",
        reload=False,
        show=not settings["native"],
    )

if __name__ in {"__main__", "__mp_main__"}:
    freeze_support()
    start()
```

`reload=False` is required for packaging.

### 5.4 Build the desktop app

```
nicegui-pack --onefile --windowed --name "SupportConsole" main.py
```

This creates `dist/SupportConsole.exe`. To hand the tool to someone, give them a folder containing:

```
SupportConsole.exe
config/
logs/
```

The `config` folder stays outside the `.exe` so queries can be edited without rebuilding. `backend/paths.py` must therefore find the base folder like this:

```python
import sys
from pathlib import Path

def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent
```

## 6. Config files

### 6.1 `config/app.yaml`

```yaml
title: "Support Console"
width: 1280
height: 800
native: true        # false = open in the default browser instead of a window
```

### 6.2 `config/connections.yaml`

FLEXCUBE, OBBRN and OBPM usually sit in different schemas (sometimes different databases), so each environment has several named schemas. Each query says which schema it runs on.

```yaml
environments:
  UAT:
    is_production: false
    allow_write: true
    schemas:
      fcubs: { host: "HOST", port: 1521, service: "SERVICE", user: "FCUBS_USER" }
      obbrn: { host: "HOST", port: 1521, service: "SERVICE", user: "OBBRN_USER" }
      obpm:  { host: "HOST", port: 1521, service: "SERVICE", user: "OBPM_USER" }
  PROD:
    is_production: true
    allow_write: false        # stays false until the bank approves write actions on PROD
    schemas:
      fcubs: { host: "HOST", port: 1521, service: "SERVICE", user: "FCUBS_USER" }
      obbrn: { host: "HOST", port: 1521, service: "SERVICE", user: "OBBRN_USER" }
      obpm:  { host: "HOST", port: 1521, service: "SERVICE", user: "OBPM_USER" }
```

A schema that the user leaves without a password at connect time is skipped, and its cards show "not connected".

### 6.3 `config/queries.yaml` format

Each entry is one dashboard card or one action.

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, lowercase with underscores |
| `title` | yes | Shown in the UI |
| `page` | yes | `dashboard` or `actions` |
| `group` | yes | Section heading in the UI |
| `schema` | yes | Key from `connections.yaml` (`fcubs`, `obbrn`, `obpm`) |
| `type` | yes | `read` or `write` |
| `sql` | yes | One statement, bind variables only, no trailing semicolon |
| `params` | no | List of `{name, label, required}` shown as input boxes |
| `display` | no | `table` (default) or `count` for a single-number card |
| `max_rows` | no | Default 500 |
| `confirm` | write only | Message shown before running |
| `expect_rows` | write only | Max rows the statement may change, default 1. More than this means rollback. |

### 6.4 Starter entries

Entries marked `VERIFY` are standard FLEXCUBE UBS names written from memory. Check column names against your version before trusting them. Entries marked `TODO` must be supplied by the bank team, because the table names differ by OBBRN / OBPM release.

```yaml
queries:

  # ---------- DASHBOARD ----------

  - id: branch_dates                      # VERIFY
    title: Branch Dates
    page: dashboard
    group: Branch
    schema: fcubs
    type: read
    sql: |
      SELECT branch_code, today, prev_working_day, next_working_day
      FROM sttm_dates
      ORDER BY branch_code

  - id: pending_txn_by_branch             # VERIFY
    title: Unauthorised Transactions by Branch
    page: dashboard
    group: Transactions
    schema: fcubs
    type: read
    sql: |
      SELECT ac_branch, module, COUNT(DISTINCT trn_ref_no) AS pending_count
      FROM actb_daily_log
      WHERE auth_stat = 'U'
      GROUP BY ac_branch, module
      ORDER BY ac_branch, module

  - id: open_teller_batches               # TODO: supply SQL
    title: Open Teller Batches
    page: dashboard
    group: Branch
    schema: obbrn
    type: read
    sql: |
      SELECT 'SQL not configured yet' AS message FROM dual

  - id: open_vault_batches                # TODO: supply SQL
    title: Open Vault Batches
    page: dashboard
    group: Branch
    schema: obbrn
    type: read
    sql: |
      SELECT 'SQL not configured yet' AS message FROM dual

  - id: approval_pending_txn              # TODO: supply SQL
    title: Transactions Awaiting Approval
    page: dashboard
    group: Transactions
    schema: obbrn
    type: read
    sql: |
      SELECT 'SQL not configured yet' AS message FROM dual

  # ---------- ACTIONS ----------

  - id: user_lookup                       # VERIFY
    title: Look Up User
    page: actions
    group: User
    schema: fcubs
    type: read
    params:
      - { name: user_id, label: "User ID", required: true }
    sql: |
      SELECT user_id, user_name, user_status, home_branch, no_successive_logins
      FROM smtb_user
      WHERE user_id = :user_id

  - id: user_clear_session                # VERIFY
    title: Clear Stuck User Session
    page: actions
    group: User
    schema: fcubs
    type: write
    confirm: "Remove the active session for this user?"
    expect_rows: 1
    params:
      - { name: user_id, label: "User ID", required: true }
    sql: |
      DELETE FROM smtb_current_users WHERE user_id = :user_id

  - id: user_password_reset               # TODO: supply the bank-approved SQL or procedure call
    title: Reset User Password
    page: actions
    group: User
    schema: fcubs
    type: write
    confirm: "Reset the password for this user?"
    expect_rows: 1
    params:
      - { name: user_id, label: "User ID", required: true }
    sql: |
      SELECT 'SQL not configured yet' AS message FROM dual

  - id: db_debug_user_on                  # VERIFY
    title: Enable DB Debug Log for User
    page: actions
    group: Logs
    schema: fcubs
    type: write
    confirm: "Enable DB debug for this user? Remember to switch it off afterwards."
    expect_rows: 200
    params:
      - { name: user_id, label: "User ID", required: true }
    sql: |
      UPDATE cstb_debug_users SET debug = 'Y' WHERE user_id = :user_id

  - id: db_debug_user_off                 # VERIFY
    title: Disable DB Debug Log for User
    page: actions
    group: Logs
    schema: fcubs
    type: write
    confirm: "Disable DB debug for this user?"
    expect_rows: 200
    params:
      - { name: user_id, label: "User ID", required: true }
    sql: |
      UPDATE cstb_debug_users SET debug = 'N' WHERE user_id = :user_id

  # TODO: add in the same pattern once SQL is supplied
  #   db_debug_system_on / db_debug_system_off
  #   obpm_log_user_on   / obpm_log_user_off   / obpm_log_system_on  / obpm_log_system_off
  #   obbrn_log_user_on  / obbrn_log_user_off  / obbrn_log_system_on / obbrn_log_system_off
```

**Note on password reset.** FLEXCUBE stores passwords hashed with a salt. A plain `UPDATE` of the password column usually leaves the user unable to log in and skips the application audit trail. Use the bank's approved method (an approved script or procedure, or the user maintenance screen). Until that SQL is supplied, the action stays as a placeholder.

## 7. Safety rules for write actions

`backend/runner.py` enforces all of these. The UI only displays them.

1. If the environment has `allow_write: false`, the backend refuses every `type: write` entry, and the UI shows the buttons disabled with the reason.
2. Every write shows a confirmation dialog with the `confirm` text, the environment name and the parameter values.
3. On an environment with `is_production: true`, the user must also type the environment name (for example `PROD`) into the dialog. The backend checks this text.
4. The statement runs with autocommit off. If the rows changed are more than `expect_rows`, roll back and return an error. Otherwise commit.
5. The result shown to the user is the rows changed and "committed" or "rolled back".
6. The registry loader rejects any entry whose SQL contains more than one statement, or a `type: read` entry that does not start with `SELECT` or `WITH`.

## 8. Audit log

Append one JSON line to `logs/audit.log` for every write attempt, including refused ones:

```json
{"time":"2026-11-16T10:42:07","windows_user":"jsmith","environment":"UAT","schema":"fcubs","db_user":"FCUBS_USER","query_id":"db_debug_user_on","params":{"user_id":"TELLER01"},"rows_affected":14,"outcome":"committed"}
```

Take `windows_user` from `getpass.getuser()`. Never log passwords.

## 9. Backend interface (`backend/service.py`)

These are the only functions the UI may call. All are plain synchronous Python and return plain dicts and lists.

| Function | Returns |
|---|---|
| `load_app_settings()` | Dict from `app.yaml` |
| `list_environments()` | List of `{name, is_production, allow_write, schemas: [names]}` |
| `connect(environment, passwords)` | `{schema_name: {"ok": bool, "error": str or None}}`. Runs `SELECT 1 FROM dual` on each schema. |
| `disconnect()` | None. Closes all pools. |
| `status()` | `{environment, is_production, allow_write, schemas: {name: bool}}` |
| `list_queries(page)` | Registry entries for `dashboard` or `actions`, without the `sql` field |
| `run_query(query_id, params, confirm_text=None)` | Reads: `{columns, rows, row_count, truncated}`. Writes: `{rows_affected, outcome}`. |
| `read_audit(limit=100)` | Latest audit entries, newest first |
| `reload_registry()` | Re-reads `queries.yaml`, returns the number of entries or raises with the validation error |

Other requirements:

- Errors are raised as one `ServiceError(message)` exception type. Oracle errors keep their code and text (for example `ORA-00942: table or view does not exist`), so the user can fix the YAML.
- `rows` must contain only strings, numbers and `None`. Convert Oracle dates to `DD-MON-YYYY` (with time when it is not midnight) and `Decimal` to `int` or `float` inside the backend.
- The backend holds one active environment at a time. This is a single-user desktop app.

## 10. User interface (NiceGUI)

One window with a header and tabs.

**Header (always visible):** tool name, environment badge, one coloured dot per schema, Disconnect button. When the environment is production, the whole header is red and reads `PRODUCTION`.

**Connect screen (shown first):** `ui.select` for the environment, one `ui.input(password=True)` per schema, Connect button. After connecting, show the tabs.

**Dashboard tab:** built from `list_queries("dashboard")`, grouped by `group`. Each card (`ui.card`) has a title, a Refresh button, the last-refreshed time, and a `ui.table` (or one large number if `display: count`). A "Refresh all" button at the top. No auto-refresh by default, to avoid load on the database. Empty results show "No rows". Errors show inside the card, and the other cards keep working.

**Actions tab:** built from `list_queries("actions")`, grouped by `group`. Each action is a small form made from `params` with a Run button. Read actions show a table underneath. Write actions open a `ui.dialog` following section 7 and then show the outcome with `ui.notify` and in the card.

**Audit tab:** table of `read_audit()` with a Refresh button.

**Settings area (small, in the header menu):** "Reload queries" button that calls `reload_registry()` and rebuilds the Dashboard and Actions tabs.

Pattern for every button that touches the database:

```python
from nicegui import run, ui
from backend import service

async def refresh_card(query_id, table, error_label):
    try:
        result = await run.io_bound(service.run_query, query_id, {})
        table.columns = [{"name": c, "label": c, "field": c, "align": "left"} for c in result["columns"]]
        table.rows = [dict(zip(result["columns"], row)) for row in result["rows"]]
        table.update()
        error_label.set_text("")
    except service.ServiceError as exc:
        error_label.set_text(str(exc))
```

## 11. Build order

Build and test one phase before starting the next.

1. **Skeleton.** Folder structure, `requirements.txt`, `app.yaml`, `main.py` opening an empty native window with the header. Test: `python main.py` opens a desktop window.
2. **Backend connections.** `paths.py`, `db.py`, `service.connect / disconnect / status`. Test from a plain Python script with no UI.
3. **Registry and read queries.** `registry.py` with validation, `runner.py` for reads, `service.list_queries / run_query`. Test from a plain Python script.
4. **Connect screen and dashboard.** Test: connect to UAT, Branch Dates card shows rows, window stays responsive during a slow query.
5. **Write actions.** Safety rules in the backend, audit log, confirm dialog, Actions tab, Audit tab. Test on UAT only.
6. **Polish.** Production styling, error display, reload queries.
7. **Package.** Run `nicegui-pack`, copy `config/` and `logs/` beside the `.exe`, test on a machine without VS Code.

## 12. Acceptance checklist

- [ ] Double-clicking `SupportConsole.exe` opens the app in its own window, with VS Code closed
- [ ] Works with no internet connection (database still reachable)
- [ ] `backend/` contains no NiceGUI imports and can be used from a plain script
- [ ] No password appears in any file or log
- [ ] Editing `config/queries.yaml` beside the `.exe` and pressing "Reload queries" shows a new card with no rebuild
- [ ] A write on PROD is refused while `allow_write: false`
- [ ] A write changing more rows than `expect_rows` is rolled back
- [ ] Every write attempt appears in the Audit tab
- [ ] A wrong table name shows the ORA error in that card, and the rest of the dashboard still works
- [ ] The window does not freeze while a query runs

## 13. How to add a new feature later

1. Open `config/queries.yaml`.
2. Copy an existing block and change `id`, `title`, `group`, `schema`, `type`, `sql` and `params`.
3. For a write, add `confirm` and `expect_rows`.
4. Save, then press "Reload queries" in the app (or restart it).

Only ask the AI assistant for code changes when a feature needs something the YAML cannot express, such as a chart, a multi-step action, or a file export. In that case add a function to `backend/service.py` first, then a UI element that calls it.

## 14. Open items to fill in before go-live

| # | Item | Owner |
|---|---|---|
| 1 | Hosts, ports, service names and DB users for UAT and PROD | You |
| 2 | Check every `VERIFY` query against your FLEXCUBE version | You |
| 3 | SQL for open teller batches and open vault batches (OBBRN) | You |
| 4 | SQL for transactions awaiting approval | You |
| 5 | Bank-approved password reset SQL or procedure | You |
| 6 | SQL for system-level DB debug on and off | You |
| 7 | SQL for OBPM and OBBRN log switches, system and per user | You |
| 8 | Decision on whether write actions are ever allowed on PROD | Bank management / IT security |
| 9 | Confirm the target PCs have the Edge WebView2 runtime (standard on Windows 10 and 11) | You / IT |
