# 05 – Deployment and Operations Runbook

How to run SolutionsHub locally, deploy it to Azure, and operate it.

---

## 1. What gets deployed

| Resource | Bicep name | Purpose |
|---|---|---|
| App Service Plan (B1 Linux) + Web App (Python 3.12) | `asp-…`, `app-…` | The application, with IP access restrictions and a system-assigned managed identity |
| Azure Database for PostgreSQL Flexible Server (B1ms) | `psql-…` | Application database `solutionshub` |
| Storage account + `attachments` container | `st…` | Uploaded files (private, soft delete, versioning) |
| Key Vault | `kv-…` | `DATABASE-URL`, `SECRET-KEY`, `ACS-CONNECTION-STRING` |
| Communication Services + Email Service (Azure-managed domain) | `acs-…`, `email-…` | Sign-in links and workflow notifications |
| Log Analytics + Application Insights | `log-…`, `appi-…` | Telemetry with a daily cap |
| Role assignments | – | Web app → Key Vault Secrets User, Storage Blob Data Contributor |

Everything is in `infra/main.bicep`; environment-specific values are in `infra/main.bicepparam`.

---

## 2. Local development

Prerequisites: Python 3.11+, PostgreSQL 16 (local install or `docker compose up -d db`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # edit DATABASE_URL, BOOTSTRAP_ADMIN_EMAIL
createdb solutionshub           # or: docker compose up -d db
python -m alembic upgrade head  # create schema
python -m app.seed              # capability taxonomy, placeholder business groups, destinations
uvicorn app.main:app --reload
```

Open http://localhost:8000. With `EMAIL_BACKEND=console` the sign-in link is printed to the console and,
in `APP_ENV=dev`, also shown on the "check your email" page. Sign in with the `BOOTSTRAP_ADMIN_EMAIL`
address to become the first Admin.

Tests and lint:

```bash
ruff check app tests
pytest -q                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/solutionshub_test pytest -q
```

---

## 3. One-time Azure setup

1. **Resource group**: `az group create -n rg-solutionshub-prod -l eastus`.
2. **Deployment identity for GitHub Actions** (OIDC, no stored secrets):
   - Create an Entra app registration, e.g. `gh-solutionshub-deploy`, and a federated credential for
     `repo:<org>/<repo>:environment:production` (subject) with issuer `https://token.actions.githubusercontent.com`.
   - Grant it **Contributor** and **User Access Administrator** on the resource group (the template creates
     role assignments). After the first infra deployment you can reduce it to **Website Contributor** on the
     web app for app deploys only.
3. **GitHub repository configuration**:
   - Secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
   - Environment `production` (optionally with required reviewers).
   - Variable `AZURE_WEBAPP_NAME` = the `webAppName` output of the infra deployment.
4. **Parameters**: edit `infra/main.bicepparam`:
   - `allowedIpRanges` – the VPN egress CIDR ranges for both corporate domains.
   - `bootstrapAdminEmail` – the first administrator.
   - `keyVaultAdminObjectId` – (optional) object id of the operator or group who should read/rotate secrets.

---

## 4. Deploy the infrastructure

Run the **Infrastructure (Bicep)** workflow (what-if first, then with `apply = true`), or from a terminal:

```bash
az deployment group what-if -g rg-solutionshub-prod -f infra/main.bicep -p infra/main.bicepparam
az deployment group create  -g rg-solutionshub-prod -f infra/main.bicep -p infra/main.bicepparam -o table
```

Outputs include `webAppName`, `webAppUrl`, `keyVaultName`, `postgresHost` and `emailSender`. Put
`webAppName` into the `AZURE_WEBAPP_NAME` repository variable.

The template generates the PostgreSQL password and the session signing key and stores them in Key Vault.
Re-running the template regenerates the default values, so on subsequent runs pass the existing values or
leave the secrets untouched by supplying `-p postgresAdminPassword=… -p appSecretKey=…` from Key Vault.

---

## 5. Deploy the application

Push to `main` (or run the **Deploy to Azure App Service** workflow). The workflow runs lint and tests,
zips `app/`, `alembic/`, `requirements.txt` and `startup.sh`, and deploys with `azure/webapps-deploy`.
App Service (Oryx) installs `requirements.txt`; `startup.sh` then runs `alembic upgrade head`, seeds
reference data, and starts Gunicorn with Uvicorn workers.

Manual alternative:

```bash
zip -r app.zip app alembic alembic.ini requirements.txt startup.sh -x '*/__pycache__/*'
az webapp deploy -g rg-solutionshub-prod -n <webAppName> --src-path app.zip --type zip
```

First start takes a few minutes while dependencies install. Check `https://<webAppName>.azurewebsites.net/health`
from a VPN-connected machine (anything else receives HTTP 403 from the access restrictions).

---

## 6. First sign-in and configuration

1. From the VPN, open the site and sign in with the bootstrap admin address. The first sign-in grants the
   Admin role automatically (only while no other Admin exists).
2. **Admin → Reference data**: replace the placeholder Business Groups with the real list; confirm
   publishing destinations.
3. **Admin → Users & roles**: grant Reviewer, Approver and Publisher roles (optionally scoped by business
   group). People do not need to have signed in first.
4. **Admin → Settings** shows the effective configuration coming from App Service settings.

---

## 7. Email deliverability (before go-live)

The template creates an **Azure-managed sender domain** (`DoNotReply@<guid>.azurecomm.net`) so email works
immediately with no DNS changes. Before go-live:

1. In the Email Communication Service, add a **custom domain** (e.g. `solutionshub.amentum.com` or a
   sub-domain of an existing sending domain) and add the TXT, SPF and DKIM records it asks for.
2. Link the verified domain to the Communication Services resource and update the `ACS_SENDER` app setting
   (e.g. `DoNotReply@solutionshub.amentum.com`).
3. Ask the mail administrators for all three recipient domains to allowlist the sender.
4. Confirm delivery to a mailbox on each of `amentum.com`, `global.amentum.com` and `amentumcms.com`.

Delivery status for every message is visible under **Admin → Notifications**, with a retry action.

---

## 8. Operations

| Task | How |
|---|---|
| Change the VPN IP allowlist | Edit `allowedIpRanges` in `infra/main.bicepparam` and re-run the infra workflow (or `az webapp config access-restriction add`). |
| View logs | `az webapp log tail -g <rg> -n <webAppName>`; Application Insights → Failures / Performance / Logs. |
| Restart | `az webapp restart -g <rg> -n <webAppName>` (migrations re-run idempotently on start). |
| Scale up | App Service Plan B1 → B2; PostgreSQL B1ms → B2s. Both are portal or CLI settings, no code change. |
| Rotate the session key | Add a new version of `SECRET-KEY` in Key Vault and restart. Users sign in again via email. |
| Rotate the DB password | Reset on the PostgreSQL server, update `DATABASE-URL` in Key Vault, restart. |
| Backups | PostgreSQL PITR (7 days); Blob soft delete and versioning (30 days). |
| Scheduler | Runs inside the web app. Jobs: deliver notifications (every minute), action reminders (hourly, weekdays), six-month review (hourly), housekeeping (03:05 UTC). A PostgreSQL advisory lock prevents duplicate runs if the plan is scaled out. |
| Disable a user | Admin → Users & roles → Disable. Revokes all sessions and blocks new sign-in links. |

---

## 9. Configuration reference

All settings are environment variables (App Service application settings). Defaults are in `app/config.py`.

| Setting | Purpose | Production value |
|---|---|---|
| `APP_ENV` | `dev` shows sign-in links on screen with the console backend; `prod` does not | `prod` |
| `BASE_URL` | Used to build links in emails; must be the public HTTPS URL | `https://<webAppName>.azurewebsites.net` |
| `SECRET_KEY` | Signs session and CSRF material | Key Vault reference |
| `DATABASE_URL` | SQLAlchemy URL, `postgresql+psycopg://…?sslmode=require` | Key Vault reference |
| `ALLOWED_EMAIL_DOMAINS` | Comma-separated sign-in domains | `amentum.com,global.amentum.com,amentumcms.com` |
| `BOOTSTRAP_ADMIN_EMAIL` | First admin | set by parameter |
| `EMAIL_BACKEND` / `ACS_CONNECTION_STRING` / `ACS_SENDER` | Email delivery | `acs`, Key Vault reference, verified sender |
| `STORAGE_BACKEND` / `AZURE_STORAGE_ACCOUNT_URL` / `AZURE_STORAGE_CONTAINER` | Attachments | `azure`, blob endpoint, `attachments` |
| `MAX_ATTACHMENTS_PER_SUBMISSION`, `MAX_ATTACHMENT_MB`, `ALLOWED_ATTACHMENT_EXTENSIONS` | Upload limits | 10, 25, see defaults |
| `REMINDER_*_DAYS`, `REVIEW_CYCLE_MONTHS`, `REVIEW_NOTICE_DAYS_BEFORE` | Workflow timing | see defaults |
| `SCHEDULER_ENABLED` | Run background jobs in this instance | `true` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry | set by template |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| HTTP 403 with an Azure-styled page before sign-in | Client IP not in the allowlist | Connect to the VPN; check egress IP against `allowedIpRanges`. |
| "Invalid or missing CSRF token" | Page left open past the session, or cookies blocked | Reload the page and retry. |
| Sign-in email never arrives | Sender domain not trusted by the recipient mail gateway | See section 7; check Admin → Notifications for the delivery status. |
| App fails to start after deploy | Migration error or missing setting | `az webapp log tail`; the startup script prints each step. Key Vault references need the web app's identity to have **Key Vault Secrets User**. |
| `/health` reports `database` error | PostgreSQL firewall or password | Check the firewall rule and the `DATABASE-URL` secret. |
