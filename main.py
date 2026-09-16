#!/usr/bin/env python3
"""
main.py — Google Workspace Bulk User Provisioning System
Terminal UI entry-point.

Usage:
    python main.py
    python main.py --credentials path/to/credentials.json
    python main.py --token path/to/token.json
"""

import argparse
import getpass
import logging
import os
import sys
import time
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.rule import Rule
from rich.text import Text
from rich import box
from rich.table import Table

# Local modules
from api import authenticate, GoogleWorkspaceAPI, AuthenticationError
from utils import (
    generate_users,
    generate_strong_password,
    mask_password,
    print_results_table,
    print_summary,
    export_csv,
    export_json,
    copy_emails_to_clipboard,
    get_successful_emails,
    CLIPBOARD_AVAILABLE,
)

# ─────────────────────────────────────────────
#  Logging Setup
# ─────────────────────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "provisioner.log"), encoding="utf-8"),
        # No StreamHandler → logs never shown in terminal (prevents accidental exposure)
    ],
)
logger = logging.getLogger("workspace_provisioner")

console = Console()

# ─────────────────────────────────────────────
#  Banner
# ─────────────────────────────────────────────

BANNER = """
[bold bright_blue]
  ██████╗  ██╗  ██╗    ██╗    ██╗ ██████╗ ██████╗ ██╗  ██╗███████╗██████╗  █████╗  ██████╗███████╗
 ██╔════╝  ██║  ██║    ██║    ██║██╔═══██╗██╔══██╗██║ ██╔╝██╔════╝██╔══██╗██╔══██╗██╔════╝██╔════╝
 ██║  ███╗ ██║  ██║    ██║ █╗ ██║██║   ██║██████╔╝█████╔╝ ███████╗██████╔╝███████║██║     █████╗  
 ██║   ██║ ██║  ██║    ██║███╗██║██║   ██║██╔══██╗██╔═██╗ ╚════██║██╔═══╝ ██╔══██║██║     ██╔══╝  
 ╚██████╔╝ ╚████╔╝     ╚███╔███╔╝╚██████╔╝██║  ██║██║  ██╗███████║██║     ██║  ██║╚██████╗███████╗
  ╚═════╝   ╚═══╝       ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝  ╚═╝ ╚═════╝╚══════╝
[/bold bright_blue]
[bold white]         Google Workspace Bulk User Provisioning System — Production Ready[/bold white]
[dim]         Uses official Admin SDK Directory API only · No bypass · No fake identities[/dim]
"""

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def header(title: str) -> None:
    console.print()
    console.print(Rule(f"[bold bright_blue]{title}[/bold bright_blue]", style="bright_blue"))
    console.print()


def success(msg: str) -> None:
    console.print(f"  [bold green]✔[/bold green]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


def error(msg: str) -> None:
    console.print(f"  [bold red]✘[/bold red]  {msg}")


def info(msg: str) -> None:
    console.print(f"  [bold cyan]ℹ[/bold cyan]  {msg}")


def secure_password_input(prompt_text: str = "Password") -> str:
    """Read password without echo, never printed to terminal."""
    return getpass.getpass(prompt=f"  {prompt_text}: ")


# ─────────────────────────────────────────────
#  Step 1 — Authentication
# ─────────────────────────────────────────────

def step_authenticate(credentials_path: str, token_path: str) -> Optional[GoogleWorkspaceAPI]:
    header("Step 1 — Google Workspace Authentication")

    console.print(
        Panel(
            "[white]Authentication uses OAuth 2.0 via the official Admin SDK.\n"
            "Your [bold]credentials.json[/bold] is never logged or shared.\n"
            "A browser window will open for one-time consent (if no valid token exists).[/white]",
            title="[bold]ℹ  How it works[/bold]",
            border_style="bright_blue",
        )
    )
    console.print()

    if not os.path.exists(credentials_path):
        error(f"credentials.json not found: [yellow]{credentials_path}[/yellow]")
        console.print(
            "\n  [dim]To get credentials.json:[/dim]\n"
            "  1. Go to [link=https://console.cloud.google.com]console.cloud.google.com[/link]\n"
            "  2. Create / open your project → APIs & Services → Credentials\n"
            "  3. Create [bold]OAuth 2.0 Client ID[/bold] (Desktop App)\n"
            "  4. Download JSON → rename to [bold]credentials.json[/bold]\n"
            "  5. Enable Admin SDK API in your project\n"
        )
        return None

    info(f"credentials.json found: [green]{credentials_path}[/green]")

    with console.status("[bold cyan]Authenticating with Google…[/bold cyan]", spinner="dots"):
        try:
            api = authenticate(credentials_path=credentials_path, token_path=token_path)
            success("Authentication successful!")
            return api
        except AuthenticationError as exc:
            error(str(exc))
            return None
        except Exception as exc:
            error(f"Unexpected error: {exc}")
            logger.exception("Authentication failed")
            return None


# ─────────────────────────────────────────────
#  Step 2 — Domain & OU Configuration
# ─────────────────────────────────────────────

def step_configure_domain(api: GoogleWorkspaceAPI) -> dict:
    header("Step 2 — Workspace Domain & Organizational Unit")

    domain = Prompt.ask(
        "  [bold]Workspace domain[/bold] (e.g. [cyan]company.com[/cyan])",
        default=os.getenv("WORKSPACE_DOMAIN", ""),
    ).strip().lower()

    if not domain or "@" in domain:
        warn("Please enter a domain only (without '@'), e.g. company.com")
        return step_configure_domain(api)

    # Fetch OUs
    console.print()
    with console.status("[bold cyan]Fetching Organizational Units…[/bold cyan]", spinner="dots"):
        ous = api.list_organizational_units()

    console.print(f"  [bold]Available Organizational Units ({len(ous)} found):[/bold]")
    console.print()

    ou_table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta", border_style="dim")
    ou_table.add_column("#",    width=5, justify="right", style="dim")
    ou_table.add_column("Name", style="white")
    ou_table.add_column("Path", style="cyan")

    for i, ou in enumerate(ous, start=1):
        ou_table.add_row(str(i), ou.get("name", ""), ou.get("orgUnitPath", "/"))

    console.print(ou_table)
    console.print()

    ou_choice = IntPrompt.ask(
        f"  Select OU number [bold](1–{len(ous)})[/bold]",
        default=1,
    )
    ou_choice = max(1, min(ou_choice, len(ous)))
    selected_ou = ous[ou_choice - 1]
    success(f"Selected OU: [cyan]{selected_ou['orgUnitPath']}[/cyan]")

    return {
        "domain":       domain,
        "ou_path":      selected_ou["orgUnitPath"],
        "ou_name":      selected_ou.get("name", ""),
    }


# ─────────────────────────────────────────────
#  Step 3 — User Parameters
# ─────────────────────────────────────────────

def step_user_parameters(domain: str) -> dict:
    header("Step 3 — User Parameters")

    console.print(
        Panel(
            "[white]Pattern tokens you can use:\n"
            "  [cyan]{n}[/cyan]     → sequential number (1, 2, 3…)\n"
            "  [cyan]{n03}[/cyan]   → zero-padded 3-digit (001, 002…)\n"
            "  [cyan]{n04}[/cyan]   → zero-padded 4-digit (0001, 0002…)\n"
            "  [cyan]{rand}[/cyan]  → random 4-digit number\n\n"
            "Examples:\n"
            "  Username prefix [cyan]user{n03}[/cyan] → user001@domain.com, user002@domain.com\n"
            "  First name      [cyan]John[/cyan]     → same for all users\n"
            "  Last name       [cyan]Doe{n03}[/cyan] → Doe001, Doe002…",
            title="[bold]Pattern Help[/bold]",
            border_style="cyan",
        )
    )
    console.print()

    count = IntPrompt.ask("  [bold]Number of users to create[/bold]", default=10)
    count = max(1, count)

    first_pattern    = Prompt.ask("  [bold]First name pattern[/bold]", default="User")
    last_pattern     = Prompt.ask("  [bold]Last name pattern[/bold]",  default="Account{n03}")
    username_prefix  = Prompt.ask("  [bold]Username prefix pattern[/bold]", default="user{n03}")

    console.print()
    console.print("  [bold]Password options:[/bold]")
    console.print("    [cyan][1][/cyan] Generate unique strong password per user (recommended)")
    console.print("    [cyan][2][/cyan] Set a single password for all users")
    console.print()

    pwd_choice = Prompt.ask("  Choose", choices=["1", "2"], default="1")

    unique_passwords = pwd_choice == "1"
    single_password  = None
    password_display = "[dim](unique per user — not shown)[/dim]"

    if not unique_passwords:
        while True:
            p1 = secure_password_input("Set password (hidden)")
            p2 = secure_password_input("Confirm password (hidden)")
            if p1 == p2 and len(p1) >= 8:
                single_password  = p1
                password_display = f"[dim]{mask_password(p1)}[/dim]"
                break
            elif p1 != p2:
                warn("Passwords do not match. Try again.")
            else:
                warn("Password must be at least 8 characters.")

    # Optional recovery info
    console.print()
    add_recovery = Confirm.ask("  Add optional recovery information?", default=False)
    recovery_email = None
    recovery_phone = None

    if add_recovery:
        recovery_email = Prompt.ask("  Recovery email (leave blank to skip)", default="").strip() or None
        console.print("  [dim]Recovery phone must be in E.164 format, e.g. +8801712345678[/dim]")
        recovery_phone = Prompt.ask("  Recovery phone (leave blank to skip)", default="").strip() or None

    # Preview first 3 generated emails
    console.print()
    preview_users = generate_users(
        count=min(3, count),
        username_prefix=username_prefix,
        first_pattern=first_pattern,
        last_pattern=last_pattern,
        domain=domain,
        unique_passwords=False,
        single_password="PREVIEW",
    )
    console.print("  [bold]Preview (first 3 accounts):[/bold]")
    for u in preview_users:
        console.print(f"    [green]→[/green] {u['primaryEmail']}  |  "
                      f"{u['name']['givenName']} {u['name']['familyName']}")
    if count > 3:
        console.print(f"    [dim]… and {count - 3} more[/dim]")
    console.print()

    return {
        "count":            count,
        "first_pattern":    first_pattern,
        "last_pattern":     last_pattern,
        "username_prefix":  username_prefix,
        "unique_passwords": unique_passwords,
        "single_password":  single_password,
        "password_display": password_display,
        "recovery_email":   recovery_email,
        "recovery_phone":   recovery_phone,
    }


# ─────────────────────────────────────────────
#  Step 4 — Confirmation
# ─────────────────────────────────────────────

def step_confirm(config: dict, domain_cfg: dict, user_params: dict) -> str:
    """
    Show a summary and ask: Dry Run / Create / Cancel.
    Returns: "dry_run" | "create" | "cancel"
    """
    header("Step 4 — Review & Confirm")

    summary_table = Table(box=box.ROUNDED, show_header=False, border_style="bright_blue", padding=(0, 2))
    summary_table.add_column("Field",  style="bold white", width=24)
    summary_table.add_column("Value",  style="cyan")

    summary_table.add_row("Domain",             domain_cfg["domain"])
    summary_table.add_row("Organizational Unit", domain_cfg["ou_path"])
    summary_table.add_row("Users to create",    str(user_params["count"]))
    summary_table.add_row("First name pattern", user_params["first_pattern"])
    summary_table.add_row("Last name pattern",  user_params["last_pattern"])
    summary_table.add_row("Username prefix",    user_params["username_prefix"])
    summary_table.add_row("Password mode",
        "Unique per user" if user_params["unique_passwords"] else "Single for all")
    summary_table.add_row("Password",           user_params["password_display"])

    if user_params.get("recovery_email"):
        summary_table.add_row("Recovery email", user_params["recovery_email"])
    if user_params.get("recovery_phone"):
        summary_table.add_row("Recovery phone", user_params["recovery_phone"])

    console.print(summary_table)
    console.print()

    console.print("  [bold]What would you like to do?[/bold]")
    console.print("    [yellow][D][/yellow] Dry Run  — simulate without creating accounts")
    console.print("    [green][C][/green] Create  — create accounts via Google API")
    console.print("    [red][X][/red] Cancel  — go back to main menu")
    console.print()

    choice = Prompt.ask("  Choose", choices=["D", "C", "X", "d", "c", "x"], default="D").upper()

    if choice == "D":
        return "dry_run"
    elif choice == "C":
        return "create"
    return "cancel"


# ─────────────────────────────────────────────
#  Step 5 — Provisioning
# ─────────────────────────────────────────────

def step_provision(
    api: GoogleWorkspaceAPI,
    users_data: List[dict],
    dry_run: bool,
) -> List[dict]:
    header("Step 5 — " + ("Dry Run Simulation" if dry_run else "Creating Users"))

    results: List[dict] = []
    total = len(users_data)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[bold white]{task.completed}/{task.total}[/bold white]"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            "[cyan]Provisioning users…" if not dry_run else "[yellow]Simulating (Dry Run)…",
            total=total,
        )

        def on_progress(current: int, total: int, result: dict) -> None:
            icon   = "✔" if result["status"] in ("SUCCESS", "DRY_RUN") else "✘"
            colour = "green" if result["status"] in ("SUCCESS", "DRY_RUN") else "red"
            progress.console.print(
                f"  [{colour}]{icon}[/{colour}]  {result['email']}  "
                f"[{colour}]{result['status']}[/{colour}]"
                + (f"  [dim]{result['error']}[/dim]" if result.get("error") else "")
            )
            progress.advance(task)

        results = api.bulk_create_users(
            users=users_data,
            dry_run=dry_run,
            progress_callback=on_progress,
        )

    return results


# ─────────────────────────────────────────────
#  Step 6 — Verification
# ─────────────────────────────────────────────

def step_verify(api: GoogleWorkspaceAPI, results: List[dict]) -> List[dict]:
    header("Step 6 — Verifying Created Accounts")

    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    if success_count == 0:
        warn("No successful accounts to verify.")
        return results

    info(f"Verifying {success_count} accounts via Directory API…")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[bold white]{task.completed}/{task.total}[/bold white]"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("[cyan]Verifying…", total=success_count)

        def on_verify(current: int, total: int, email: str, verification: dict) -> None:
            status_str = verification.get("status", "UNKNOWN")
            colour = "green" if status_str == "ACTIVE" else "red"
            progress.console.print(
                f"  [cyan]→[/cyan]  {email}  [{colour}]{status_str}[/{colour}]"
            )
            progress.advance(task)

        results = api.verify_all_users(results, progress_callback=on_verify)

    return results


# ─────────────────────────────────────────────
#  Post-Creation Menu
# ─────────────────────────────────────────────

def post_creation_menu(results: List[dict]) -> None:
    while True:
        header("Results")
        print_results_table(results)
        print_summary(results)

        console.print("  [bold]Options:[/bold]")
        console.print("    [cyan][1][/cyan] Show successful users")
        console.print("    [cyan][2][/cyan] Show failed users")
        console.print("    [cyan][3][/cyan] Copy all successful emails to clipboard")
        console.print("    [cyan][4][/cyan] Export CSV")
        console.print("    [cyan][5][/cyan] Export JSON")
        console.print("    [cyan][6][/cyan] Retry failed users")
        console.print("    [cyan][7][/cyan] Show results with passwords")
        console.print("    [cyan][8][/cyan] Exit")
        console.print()

        choice = Prompt.ask("  Choose", choices=["1","2","3","4","5","6","7","8"], default="8")

        if choice == "1":
            success_results = [r for r in results if r["status"] == "SUCCESS"]
            header(f"Successful Users ({len(success_results)})")
            print_results_table(success_results)
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "2":
            failed_results = [r for r in results if r["status"] not in ("SUCCESS", "DRY_RUN")]
            header(f"Failed Users ({len(failed_results)})")
            if failed_results:
                print_results_table(failed_results)
            else:
                success("No failed users!")
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "3":
            emails = get_successful_emails(results)
            if not emails:
                warn("No successful users to copy.")
            elif not CLIPBOARD_AVAILABLE:
                warn("pyperclip not available — printing emails instead:")
                console.print("\n".join(emails))
            else:
                ok = copy_emails_to_clipboard(results)
                if ok:
                    success(f"Copied {len(emails)} email(s) to clipboard!")
                else:
                    warn("Clipboard copy failed. Printing instead:")
                    console.print("\n".join(emails))
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "4":
            incl = Confirm.ask("  Include passwords in CSV?", default=False)
            fp = export_csv(results, include_passwords=incl)
            success(f"CSV exported: [green]{fp}[/green]")
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "5":
            incl = Confirm.ask("  Include passwords in JSON?", default=False)
            fp = export_json(results, include_passwords=incl)
            success(f"JSON exported: [green]{fp}[/green]")
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "6":
            failed = [r for r in results if r["status"] == "FAILED"]
            if not failed:
                success("No failed users to retry.")
            else:
                warn(f"Retry feature: re-run the provisioner with only the {len(failed)} failed users.")
                console.print("  [dim]Tip: failed user emails are exported in the CSV/JSON above.[/dim]")
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "7":
            warn("⚠  Passwords will appear on screen. Ensure no one is watching.")
            if Confirm.ask("  Proceed?", default=False):
                print_results_table(results, show_passwords=True)
            Prompt.ask("\n  [dim]Press Enter to continue[/dim]")

        elif choice == "8":
            console.print()
            console.print(Panel("[bold green]Done. Goodbye![/bold green]", border_style="green"))
            break


# ─────────────────────────────────────────────
#  Main Entry Point
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Google Workspace Bulk User Provisioner")
    parser.add_argument(
        "--credentials",
        default=os.getenv("GW_CREDENTIALS_PATH", "credentials.json"),
        help="Path to credentials.json (default: credentials.json in current dir)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("GW_TOKEN_PATH", "token.json"),
        help="Path to token.json (default: token.json in current dir)",
    )
    args = parser.parse_args()

    clear()
    console.print(BANNER)
    console.print(
        Panel(
            "[bold red]⚠  IMPORTANT[/bold red]\n\n"
            "[white]This tool works ONLY with your own authorized Google Workspace organization.\n"
            "It uses the official Admin SDK Directory API.\n"
            "No verification bypass · No fake identities · No @gmail.com automation.[/white]",
            border_style="red",
        )
    )
    console.print()

    if not Confirm.ask("  I confirm I am the Workspace Admin and I own this domain", default=False):
        console.print("\n  [dim]Exiting. Only authorized Workspace Admins may use this tool.[/dim]\n")
        sys.exit(0)

    # ── Auth ───────────────────────────────────
    api = step_authenticate(args.credentials, args.token)
    if not api:
        error("Authentication failed. Exiting.")
        sys.exit(1)

    Prompt.ask("\n  [dim]Press Enter to continue[/dim]")
    clear()

    # ── Domain / OU ───────────────────────────
    domain_cfg = step_configure_domain(api)

    Prompt.ask("\n  [dim]Press Enter to continue[/dim]")
    clear()

    # ── User Parameters ───────────────────────
    user_params = step_user_parameters(domain_cfg["domain"])

    Prompt.ask("\n  [dim]Press Enter to continue[/dim]")
    clear()

    # ── Confirm ───────────────────────────────
    action = step_confirm({}, domain_cfg, user_params)
    if action == "cancel":
        console.print("\n  [dim]Cancelled. Exiting.[/dim]\n")
        sys.exit(0)

    dry_run = action == "dry_run"
    clear()

    # ── Generate user data ────────────────────
    users_data = generate_users(
        count=user_params["count"],
        username_prefix=user_params["username_prefix"],
        first_pattern=user_params["first_pattern"],
        last_pattern=user_params["last_pattern"],
        domain=domain_cfg["domain"],
        org_unit_path=domain_cfg["ou_path"],
        single_password=user_params["single_password"],
        unique_passwords=user_params["unique_passwords"],
        recovery_email=user_params.get("recovery_email"),
        recovery_phone=user_params.get("recovery_phone"),
    )

    # ── Provision ────────────────────────────
    results = step_provision(api, users_data, dry_run=dry_run)

    # ── Verify (skip for dry run) ─────────────
    if not dry_run:
        if Confirm.ask("\n  Verify all created accounts via API?", default=True):
            results = step_verify(api, results)

    # ── Post-creation menu ────────────────────
    post_creation_menu(results)


if __name__ == "__main__":
    main()
