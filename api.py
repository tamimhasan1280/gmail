"""
api.py — Google Workspace Admin SDK / Directory API wrapper.

Handles:
  • OAuth2 authentication (credentials.json / token.json)
  • Listing Organizational Units
  • Controlled bulk user creation with rate-limiting & retry
  • Per-user verification after creation
  • Error classification (SUCCESS / FAILED / RETRYABLE)
"""

import logging
import os
import time
from datetime import datetime
from typing import Callable, List, Optional

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────

# Minimum required scopes — no extra permissions requested.
SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
]

# Google Workspace Directory API rate limits (conservative):
#   • 10 write requests / second per domain
#   • 2 000 write requests / day (varies by edition)
REQUESTS_PER_SECOND = 5          # stay well under the 10/s limit
BATCH_SIZE          = 10         # pause between batches
BATCH_PAUSE_SECONDS = 2.0        # seconds between batches
RETRY_WAIT_SECONDS  = 5.0        # wait before retry
MAX_RETRIES         = 3

# HTTP status codes that are transient and safe to retry
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

logger = logging.getLogger("workspace_provisioner")


# ─────────────────────────────────────────────
#  Authentication
# ─────────────────────────────────────────────

class AuthenticationError(Exception):
    pass


def authenticate(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> "GoogleWorkspaceAPI":
    """
    Perform OAuth2 authentication and return an API client.

    • If a valid token.json exists → reuse / auto-refresh.
    • Otherwise → launch browser-based consent flow.
    • credentials.json and token.json are NEVER logged or printed.
    """
    if not os.path.exists(credentials_path):
        raise AuthenticationError(
            f"credentials.json not found at: {credentials_path}\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )

    creds: Optional[Credentials] = None

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as exc:
            logger.warning("Could not load token.json: %s — will re-authenticate.", exc)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Access token refreshed successfully.")
            except GoogleAuthError as exc:
                logger.warning("Token refresh failed (%s) — re-authenticating.", exc)
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        # Persist token for future runs (file permissions set by install.sh)
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())
        logger.info("Token saved to %s", token_path)

    service = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
    return GoogleWorkspaceAPI(service)


# ─────────────────────────────────────────────
#  API Client
# ─────────────────────────────────────────────

class GoogleWorkspaceAPI:
    """Thin, rate-limited wrapper around the Admin SDK Directory API."""

    def __init__(self, service) -> None:
        self._service = service
        self._last_request_time: float = 0.0

    # ── internal throttle ───────────────────

    def _throttle(self) -> None:
        """Enforce per-second request rate."""
        min_gap = 1.0 / REQUESTS_PER_SECOND
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_request_time = time.monotonic()

    # ── Organizational Units ─────────────────

    def list_organizational_units(self, customer_id: str = "my_customer") -> List[dict]:
        """
        Return a list of OUs: [{"name": "...", "orgUnitPath": "...", ...}]
        Root OU ("/") is always prepended.
        """
        self._throttle()
        try:
            resp = (
                self._service.orgunits()
                .list(customerId=customer_id, type="all")
                .execute()
            )
            ous = resp.get("organizationUnits", [])
            # Sort by path for readability
            ous.sort(key=lambda x: x.get("orgUnitPath", ""))
            # Prepend root
            root = {"name": "/ (Root)", "orgUnitPath": "/"}
            return [root] + ous
        except HttpError as exc:
            logger.error("Failed to list OUs: %s", exc)
            return [{"name": "/ (Root)", "orgUnitPath": "/"}]

    # ── Single User Creation ─────────────────

    def _create_user_once(self, user_data: dict) -> dict:
        """
        Call users.insert for one user.
        Returns the API response dict on success.
        Raises HttpError on failure.
        """
        # Never log the password field
        safe_log = {k: v for k, v in user_data.items() if k != "password"}
        logger.debug("Creating user: %s", safe_log)

        self._throttle()
        return self._service.users().insert(body=user_data).execute()

    def create_user(self, user_data: dict) -> dict:
        """
        Create a single user with retry logic.

        Returns a result dict:
          {
            "email":      str,
            "full_name":  str,
            "status":     "SUCCESS" | "FAILED",
            "ou":         str,
            "created_at": ISO-string | "",
            "error":      str | "",
            "password":   str,          # kept in memory only
            "error_type": "NONE" | "RETRYABLE" | "PERMANENT",
          }
        """
        email     = user_data.get("primaryEmail", "")
        full_name = user_data.get("name", {}).get("fullName", "")
        password  = user_data.get("password", "")  # never written to disk

        result = {
            "email":      email,
            "full_name":  full_name,
            "status":     "FAILED",
            "ou":         user_data.get("orgUnitPath", "/"),
            "created_at": "",
            "error":      "",
            "password":   password,
            "error_type": "NONE",
        }

        last_error: Optional[str] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._create_user_once(user_data)
                result["status"]     = "SUCCESS"
                result["created_at"] = response.get("creationTime", datetime.utcnow().isoformat())
                result["ou"]         = response.get("orgUnitPath", result["ou"])
                logger.info("Created: %s", email)
                return result

            except HttpError as exc:
                status_code = exc.resp.status
                msg = str(exc.reason) if hasattr(exc, "reason") else str(exc)

                if status_code in RETRYABLE_HTTP_CODES:
                    last_error = f"HTTP {status_code}: {msg}"
                    result["error_type"] = "RETRYABLE"
                    if attempt < MAX_RETRIES:
                        wait = RETRY_WAIT_SECONDS * attempt
                        logger.warning(
                            "Retryable error for %s (attempt %d/%d): %s — waiting %.1fs",
                            email, attempt, MAX_RETRIES, msg, wait,
                        )
                        time.sleep(wait)
                    else:
                        result["error"] = last_error
                        logger.error("Permanent failure for %s after %d attempts: %s", email, MAX_RETRIES, last_error)

                else:
                    # Permanent error — do not retry
                    result["error"]      = f"HTTP {status_code}: {msg}"
                    result["error_type"] = "PERMANENT"
                    logger.error("Permanent error for %s: %s", email, result["error"])
                    return result

            except Exception as exc:
                result["error"]      = str(exc)
                result["error_type"] = "PERMANENT"
                logger.exception("Unexpected error creating %s", email)
                return result

        return result

    # ── User Verification ────────────────────

    def verify_user(self, email: str) -> dict:
        """
        Verify a user exists by fetching from the API.

        Returns:
          {
            "exists":      bool,
            "email":       str,
            "suspended":   bool,
            "ou":          str,
            "status":      "ACTIVE" | "SUSPENDED" | "NOT_FOUND",
          }
        """
        self._throttle()
        try:
            user = self._service.users().get(userKey=email).execute()
            return {
                "exists":    True,
                "email":     user.get("primaryEmail", email),
                "suspended": user.get("suspended", False),
                "ou":        user.get("orgUnitPath", "/"),
                "status":    "SUSPENDED" if user.get("suspended") else "ACTIVE",
            }
        except HttpError as exc:
            if exc.resp.status == 404:
                return {"exists": False, "email": email, "suspended": False, "ou": "", "status": "NOT_FOUND"}
            raise

    # ── Bulk Creation ────────────────────────

    def bulk_create_users(
        self,
        users: List[dict],
        dry_run: bool = False,
        progress_callback: Optional[Callable[[int, int, dict], None]] = None,
    ) -> List[dict]:
        """
        Create multiple users with controlled batching.

        Args:
            users:             List of user_data dicts from utils.generate_users().
            dry_run:           If True, simulate without calling the API.
            progress_callback: Called after each user: fn(current, total, result_dict).

        Returns:
            List of result dicts (one per user).
        """
        results: List[dict] = []
        total = len(users)

        for idx, user_data in enumerate(users, start=1):
            email     = user_data.get("primaryEmail", "")
            full_name = user_data.get("name", {}).get("fullName", "")

            if dry_run:
                result = {
                    "email":      email,
                    "full_name":  full_name,
                    "status":     "DRY_RUN",
                    "ou":         user_data.get("orgUnitPath", "/"),
                    "created_at": "",
                    "error":      "",
                    "password":   user_data.get("password", ""),
                    "error_type": "NONE",
                }
            else:
                result = self.create_user(user_data)

            results.append(result)

            if progress_callback:
                progress_callback(idx, total, result)

            # Batch pause every BATCH_SIZE requests
            if not dry_run and idx % BATCH_SIZE == 0 and idx < total:
                logger.debug("Batch pause after %d requests (%.1fs)", idx, BATCH_PAUSE_SECONDS)
                time.sleep(BATCH_PAUSE_SECONDS)

        return results

    # ── Verify All ───────────────────────────

    def verify_all_users(
        self,
        results: List[dict],
        progress_callback: Optional[Callable[[int, int, str, dict], None]] = None,
    ) -> List[dict]:
        """
        Re-check every SUCCESS result against the live API.
        Updates each result's status / ou / error fields in-place.
        Returns the updated list.
        """
        success_results = [r for r in results if r.get("status") == "SUCCESS"]
        total = len(success_results)

        for idx, r in enumerate(success_results, start=1):
            try:
                verification = self.verify_user(r["email"])
                if not verification["exists"]:
                    r["status"] = "FAILED"
                    r["error"]  = "Account not found during verification"
                elif verification["suspended"]:
                    r["status"] = "SUSPENDED"
                    r["error"]  = "Account was created but is suspended"
                # else: stays SUCCESS

                if progress_callback:
                    progress_callback(idx, total, r["email"], verification)

            except Exception as exc:
                logger.warning("Verification error for %s: %s", r["email"], exc)

            # throttle verification calls too
            if idx % BATCH_SIZE == 0 and idx < total:
                time.sleep(BATCH_PAUSE_SECONDS)

        return results
