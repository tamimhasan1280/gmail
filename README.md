# Google Workspace Bulk User Provisioning System

Production-ready CLI tool for bulk-creating users in your own authorized
**Google Workspace** organization via the official **Admin SDK Directory API**.

> ⚠ **This tool works ONLY with your own Workspace domain.**  
> It does NOT automate `@gmail.com` accounts, bypass any verification,
> or use any mechanism outside Google's official APIs.

---

## Project Structure

```
workspace-provisioner/
├── main.py            # Terminal UI — all interactive menus & orchestration
├── api.py             # Google Workspace Directory API wrapper (auth, create, verify)
├── utils.py           # Password generation, user-data builder, table, CSV/JSON export
├── requirements.txt   # Python dependencies
├── .env.example       # Config template — copy to .env and edit
├── install.sh         # Ubuntu/Debian VPS one-shot installer
├── run.sh             # Launch wrapper (activates venv + loads .env)
├── .gitignore         # Excludes credentials.json, token.json, .env, logs/, exports/
├── logs/              # Rotating log files (never contains passwords)
└── exports/           # CSV / JSON exports (gitignored)
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Google Workspace Admin** | You must be a Super Admin of the domain |
| **Google Cloud Project** | With Admin SDK API enabled |
| **OAuth 2.0 credentials** | `credentials.json` (Desktop App type) |
| **Python 3.9+** | Installed on your VPS |

---

## Quick Setup (Ubuntu / Debian VPS)

### Step 1 — Clone and install

```bash
git clone https://github.com/tamimhasan1280/gmail.git
cd gmail
chmod +x install.sh run.sh
sudo ./install.sh
```

### Step 2 — Google Cloud Console setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create or select a project
3. Enable **Admin SDK API**:
   - APIs & Services → Library → search "Admin SDK" → Enable
4. Create credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: **Desktop App**
   - Download JSON → rename to **`credentials.json`**
5. Copy to your project directory:

```bash
cp ~/credentials.json /path/to/gmail/credentials.json
chmod 600 /path/to/gmail/credentials.json
```

### Step 3 — Configure environment

```bash
cp .env.example .env
nano .env
# Set: WORKSPACE_DOMAIN=yourcompany.com
chmod 600 .env
```

### Step 4 — First run (authentication)

```bash
./run.sh
```

A browser window will open for Google OAuth consent (first time only).  
After approval, `token.json` is saved automatically.

**Verify setup:**
```bash
ls -la credentials.json token.json   # should exist, permissions 600
cat logs/provisioner.log             # check for errors
```

---

## Usage

```bash
./run.sh
```

Interactive menu guides you through:

1. **Authentication** — OAuth2 (browser consent, one-time)
2. **Domain & OU** — Select your Workspace domain and Organizational Unit
3. **User parameters** — Count, name patterns, username prefix, password mode
4. **Review** — Dry Run (simulate) or Create (live API calls)
5. **Progress** — Real-time per-user status with Rich progress bar
6. **Verification** — Each account re-checked via API after creation
7. **Results** — Table + export options

### Pattern tokens

| Token | Result |
|---|---|
| `{n}` | 1, 2, 3… |
| `{n03}` | 001, 002, 003… |
| `{n04}` | 0001, 0002… |
| `{rand}` | random 4-digit number |

**Example:**
```
Username prefix:  user{n03}
First name:       John
Last name:        Doe{n03}
Domain:           company.com

→ user001@company.com  (John Doe001)
→ user002@company.com  (John Doe002)
```

---

## Security

| Topic | Implementation |
|---|---|
| Passwords | Never written to log files or terminal |
| credentials.json | Never logged or committed to Git |
| token.json | Stored with 600 permissions, gitignored |
| .env | 600 permissions, gitignored |
| logs/ | Never contains any credential or password data |
| exports/ | Password column only added when explicitly requested |

---

## Rate Limiting

Follows Google's API quotas:
- Max **5 requests/second** (well under 10/s limit)
- **2-second pause** every 10 users
- **Automatic retry** (up to 3x) for transient errors (429, 5xx)
- Permanent errors (400, 403, 409) reported immediately without retry

---

## Export Formats

**CSV** (`exports/provisioning_YYYYMMDD_HHMMSS.csv`):
```
email,full_name,status,org_unit,created_at,error
user001@company.com,John Doe001,SUCCESS,/,2024-01-15T10:30:00Z,
```

**JSON** (`exports/provisioning_YYYYMMDD_HHMMSS.json`):
```json
{
  "exported_at": "2024-01-15T10:30:00",
  "total": 100,
  "created": 97,
  "failed": 3,
  "users": [...]
}
```

---

## Update Procedure

```bash
cd /path/to/gmail
git pull origin main
./venv/bin/pip install -r requirements.txt
```

---

## Important Limitations

- Works **only** with Google Workspace (paid) domains — not `@gmail.com`
- Requires **Super Admin** role in your Workspace organization
- Daily user creation limits apply per your Workspace edition
- First run requires browser access for OAuth2 consent

---

## License

MIT — for use only with authorized Workspace domains you own or administer.
