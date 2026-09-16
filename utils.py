"""
utils.py — Password generation, user data building,
            table display, CSV/JSON export, clipboard copy.
"""

import csv
import json
import os
import random
import secrets
import string
from datetime import datetime
from typing import List, Optional

try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


# ─────────────────────────────────────────────
#  Password Utilities
# ─────────────────────────────────────────────

def generate_strong_password(length: int = 16) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        # Ensure at least one of each required character class
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*()-_=+" for c in pwd)
        ):
            return pwd


def mask_password(password: str) -> str:
    """Return masked version for display only."""
    if len(password) <= 4:
        return "*" * len(password)
    return password[:2] + "*" * (len(password) - 4) + password[-2:]


# ─────────────────────────────────────────────
#  User Data Generation
# ─────────────────────────────────────────────

def _resolve_pattern(pattern: str, index: int) -> str:
    """
    Replace placeholder tokens in a pattern string.

    Supported tokens:
      {n}    → sequential number (1-based, no padding)
      {n03}  → zero-padded 3-digit number (001, 002 …)
      {n04}  → zero-padded 4-digit number
      {rand} → 4-digit random number
    """
    s = pattern
    s = s.replace("{n}", str(index))
    s = s.replace("{n03}", str(index).zfill(3))
    s = s.replace("{n04}", str(index).zfill(4))
    s = s.replace("{rand}", str(random.randint(1000, 9999)))
    return s


def generate_users(
    count: int,
    username_prefix: str,
    first_pattern: str,
    last_pattern: str,
    domain: str,
    org_unit_path: str = "/",
    single_password: Optional[str] = None,
    unique_passwords: bool = False,
    recovery_email: Optional[str] = None,
    recovery_phone: Optional[str] = None,
    start_index: int = 1,
) -> List[dict]:
    """
    Build a list of user-data dicts ready for the Directory API.
    Passwords are stored only in memory; never written to disk here.
    """
    users = []
    for i in range(start_index, start_index + count):
        first_name = _resolve_pattern(first_pattern, i)
        last_name  = _resolve_pattern(last_pattern,  i)
        username   = _resolve_pattern(username_prefix, i)
        email      = f"{username}@{domain}"

        password = generate_strong_password() if unique_passwords else (single_password or generate_strong_password())

        user_data: dict = {
            "primaryEmail": email,
            "name": {
                "givenName":  first_name,
                "familyName": last_name,
                "fullName":   f"{first_name} {last_name}",
            },
            "password": password,          # kept in memory only
            "changePasswordAtNextLogin": False,
            "orgUnitPath": org_unit_path,
        }

        if recovery_email:
            user_data["recoveryEmail"] = recovery_email

        if recovery_phone:
            # E.164 format required by Directory API
            user_data["recoveryPhone"] = recovery_phone

        users.append(user_data)

    return users


# ─────────────────────────────────────────────
#  Rich Table Display
# ─────────────────────────────────────────────

STATUS_STYLE = {
    "SUCCESS":   "[bold green]SUCCESS[/bold green]",
    "FAILED":    "[bold red]FAILED[/bold red]",
    "DRY_RUN":   "[bold yellow]DRY RUN[/bold yellow]",
    "RETRYING":  "[bold cyan]RETRYING[/bold cyan]",
}


def print_results_table(results: List[dict], show_passwords: bool = False) -> None:
    """Render a Rich table with creation results."""
    table = Table(
        title="[bold white]📋 Provisioning Results[/bold white]",
        box=box.DOUBLE_EDGE,
        show_header=True,
        header_style="bold magenta",
        border_style="bright_blue",
        expand=False,
    )

    table.add_column("#",            style="dim", width=5,  justify="right")
    table.add_column("Email",        style="cyan", min_width=30)
    table.add_column("Full Name",    style="white", min_width=20)
    table.add_column("Status",       justify="center", min_width=10)
    table.add_column("Detail",       style="dim", min_width=20)

    if show_passwords:
        table.add_column("Password", style="yellow", min_width=18)

    for idx, r in enumerate(results, start=1):
        status_str = STATUS_STYLE.get(r.get("status", "FAILED"), r.get("status", ""))
        detail     = r.get("error", r.get("ou", "")) or ""
        if len(detail) > 40:
            detail = detail[:37] + "…"

        row = [
            str(idx),
            r.get("email", ""),
            r.get("full_name", ""),
            status_str,
            detail,
        ]

        if show_passwords:
            row.append(r.get("password", ""))

        table.add_row(*row)

    console.print(table)


def print_summary(results: List[dict]) -> None:
    """Print summary counts."""
    total   = len(results)
    created = sum(1 for r in results if r.get("status") == "SUCCESS")
    failed  = total - created

    console.print()
    console.print(f"  [bold]Total Requested:[/bold]  [white]{total}[/white]")
    console.print(f"  [bold]Created:[/bold]          [green]{created}[/green]")
    console.print(f"  [bold]Failed:[/bold]           [red]{failed}[/red]")
    console.print()


# ─────────────────────────────────────────────
#  Export Utilities
# ─────────────────────────────────────────────

def _ensure_exports_dir() -> str:
    exports_dir = os.path.join(os.path.dirname(__file__), "exports")
    os.makedirs(exports_dir, exist_ok=True)
    return exports_dir


def export_csv(results: List[dict], include_passwords: bool = False) -> str:
    """Export results to CSV. Returns the file path."""
    exports_dir = _ensure_exports_dir()
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(exports_dir, f"provisioning_{ts}.csv")

    fieldnames = ["email", "full_name", "status", "org_unit", "created_at", "error"]
    if include_passwords:
        fieldnames.append("password")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {
                "email":      r.get("email", ""),
                "full_name":  r.get("full_name", ""),
                "status":     r.get("status", ""),
                "org_unit":   r.get("ou", ""),
                "created_at": r.get("created_at", ""),
                "error":      r.get("error", ""),
            }
            if include_passwords:
                row["password"] = r.get("password", "")
            writer.writerow(row)

    return filepath


def export_json(results: List[dict], include_passwords: bool = False) -> str:
    """Export results to JSON. Returns the file path."""
    exports_dir = _ensure_exports_dir()
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(exports_dir, f"provisioning_{ts}.json")

    total   = len(results)
    created = sum(1 for r in results if r.get("status") == "SUCCESS")
    failed  = total - created

    clean_results = []
    for r in results:
        entry = {
            "email":      r.get("email", ""),
            "full_name":  r.get("full_name", ""),
            "status":     r.get("status", ""),
            "org_unit":   r.get("ou", ""),
            "created_at": r.get("created_at", ""),
        }
        if r.get("error"):
            entry["error"] = r["error"]
        if include_passwords:
            entry["password"] = r.get("password", "")
        clean_results.append(entry)

    payload = {
        "exported_at": datetime.now().isoformat(),
        "total":   total,
        "created": created,
        "failed":  failed,
        "users":   clean_results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return filepath


# ─────────────────────────────────────────────
#  Clipboard
# ─────────────────────────────────────────────

def copy_emails_to_clipboard(results: List[dict]) -> bool:
    """Copy all successful emails to clipboard. Returns True on success."""
    if not CLIPBOARD_AVAILABLE:
        return False
    emails = [r["email"] for r in results if r.get("status") == "SUCCESS"]
    if not emails:
        return False
    try:
        pyperclip.copy("\n".join(emails))
        return True
    except Exception:
        return False


def get_successful_emails(results: List[dict]) -> List[str]:
    return [r["email"] for r in results if r.get("status") == "SUCCESS"]
