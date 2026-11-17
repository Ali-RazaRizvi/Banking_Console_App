"""Test a saved connection from the command line, with no UI.

Usage (from the project folder):
    .venv\\Scripts\\python tools\\check_connection.py TESTDB14

You are asked for each schema's password (input is hidden). Press Enter to skip a schema.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import service  # noqa: E402


def main() -> int:
    profiles = {p["alias"]: p for p in service.list_profiles()}
    if len(sys.argv) < 2 or sys.argv[1] not in profiles:
        print("Usage: python tools/check_connection.py <alias>")
        print("Saved aliases:", ", ".join(profiles) or "(none - connect once from the app with 'Save' ticked)")
        return 2
    profile = profiles[sys.argv[1]]

    passwords = {}
    for role, cfg in profile["schemas"].items():
        prompt = f"Password for {role} ({cfg['user']} @ {service.describe_target(cfg)}), Enter to skip: "
        passwords[role] = getpass.getpass(prompt)

    print(f"\nConnecting to {profile['alias']} ({profile['env_type']})...")
    results = service.connect(profile, passwords)
    passwords.clear()

    for name, res in results.items():
        if res["skipped"]:
            print(f"  {name:8} SKIPPED")
        elif res["ok"]:
            print(f"  {name:8} OK")
        else:
            print(f"  {name:8} FAILED  {res['error']}")

    print("\nStatus:", service.status())
    service.disconnect()
    return 0 if any(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except service.ServiceError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
