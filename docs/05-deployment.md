# 05 – Deployment and Operations Runbook

How to run SolutionsHub locally, deploy it to Azure, and operate it.

---

## 1. What gets deployed

| Resource | Bicep name | Purpose |
|---|---|---|
| App Service Plan (B1 Linux) + Web App (Python 3.12) | `asp-…`, `app-…` | The application (public HTTPS endpoint) |
| Azure Database for PostgreSQL Flexible Server (B1ms) | `psql-…` | Application database `solutionshub` |
| Storage account + `attachments` container | `st…` | Uploaded files (private, soft delete, versioning) |
| Communication Services + Email Service (Azure-managed domain) | `acs-…`, `email-…` | Sign-in links and workflow notifications |
| Log Analytics + Application Insights | `log-…`, `appi-…` | Telemetry with a daily cap |

Everything is in `infra/main.bicep`; environment-specific values are in `infra/main.bicepparam`.

The template needs only **Contributor** on the resource group: it creates no role assignments and no Key
Vault. Service credentials (PostgreSQL password, storage account key, Communication Services connection
string, session signing key) are generated or read by the template and written to App Service application
settings.

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

1. **Resource group** (or use the one you have Contributor on): `az group create -n rg-solutionshub-prod -l eastus`.
2. **Parameters**: edit `infra/main.bicepparam`:
   - `bootstrapAdminEmail` – the first administrator.
   - `allowedEmailDomains` – already set to the three Amentum domains.
3. **GitHub repository configuration** (after the infra deployment, section 4):
   - Secret `AZURE_WEBAPP_PUBLISH_PROFILE` – output of
     `az webapp deployment list-publishing-profiles -g <rg> -n <webAppName> --xml`.
   - Variable `AZURE_WEBAPP_NAME` – the `webAppName` output of the infra deployment.
   - Environment `production` (optionally with required reviewers).

No Entra app registration, service principal or role assignment is required.

## 4. Deploy the infrastructure

From a workstation with the Azure CLI, signed in as a Contributor on the resource group:

```bash
az deployment group what-if -g rg-solutionshub-prod -f infra/main.bicep -p infra/main.bicepparam
az deployment group create  -g rg-solutionshub-prod -f infra/main.bicep -p infra/main.bicepparam -o table
```

Outputs include `webAppName`, `webAppUrl`, `postgresHost`, `storageAccountName` and `emailSender`. Put
`webAppName` into the `AZURE_WEBAPP_NAME` repository variable.

The **Infrastructure (Bicep)** GitHub workflow does the same but needs a service principal or OIDC credential
with Contributor on the group; creating that credential's role assignment needs Owner / User Access
Administrator, so it is optional.

The template generates the PostgreSQL password and the session signing key on the first run and writes them
to the web app's settings. Re-running with the defaults would regenerate them, so on subsequent runs pass the
existing values, read from the current app settings:

```bash
SECRET=$(az webapp config appsettings list -g <rg> -n <webAppName> --query "[?name=='SECRET_KEY'].value" -o tsv)
# The PostgreSQL password is inside DATABASE_URL; keep a copy from the first deployment.
az deployment group create -g <rg> -f infra/main.bicep -p infra/main.bicepparam -p appSecretKey="$SECRET" -p postgresAdminPassword="$PG_PW"
```

---

## 5. Deploy the application

Push to `main` (or run the **Deploy to Azure App Service** workflow). The workflow runs lint and tests,
zips `app/`, `alembic/`, `requirements.txt` and `startup.sh`, and deploys with `azure/webapps-deploy` using
the publish profile.
App Service (Oryx) installs `requirements.txt`; `startup.sh` then runs `alembic upgrade head`, seeds
reference data, and starts Gunicorn with Uvicorn workers.

Manual alternative:

```bash
zip -r app.zip app alembic alembic.ini requirements.txt startup.sh -x '*/__pycache__/*'
az webapp deploy -g rg-solutionshub-prod -n <webAppName> --src-path app.zip --type zip
```

First start takes a few minutes while dependencies install. Check `https://<webAppName>.azurewebsites.net/health`.

---

## 6. First sign-in and configuration

1. Open the site and sign in with the bootstrap admin address. The first sign-in grants the
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
| View logs | `az webapp log tail -g <rg> -n <webAppName>`; Application Insights → Failures / Performance / Logs. |
| Restart | `az webapp restart -g <rg> -n <webAppName>` (migrations re-run idempotently on start). |
| Scale up | App Service Plan B1 → B2; PostgreSQL B1ms → B2s. Both are portal or CLI settings, no code change. |
| Rotate the session key | Update the `SECRET_KEY` app setting (App Service restarts automatically). Users sign in again via email. |
| Rotate the DB password | Reset on the PostgreSQL server, update the `DATABASE_URL` app setting. |
| Rotate the storage key | Regenerate key2, update `AZURE_STORAGE_CONNECTION_STRING`, then regenerate key1. |
| Restrict access by network later | `az webapp config access-restriction add` (App Service IP restrictions) or Front Door + WAF; no app change needed. |
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
| `SECRET_KEY` | Signs session and CSRF material | generated by the template |
| `DATABASE_URL` | SQLAlchemy URL, `postgresql+psycopg://…?sslmode=require` | built by the template |
| `ALLOWED_EMAIL_DOMAINS` | Comma-separated sign-in domains | `amentum.com,global.amentum.com,amentumcms.com` |
| `BOOTSTRAP_ADMIN_EMAIL` | First admin | set by parameter |
| `EMAIL_BACKEND` / `ACS_CONNECTION_STRING` / `ACS_SENDER` | Email delivery | `acs`, from `listKeys`, verified sender |
| `STORAGE_BACKEND` / `AZURE_STORAGE_CONNECTION_STRING` / `AZURE_STORAGE_CONTAINER` | Attachments | `azure`, account connection string, `attachments` |
| `MAX_ATTACHMENTS_PER_SUBMISSION`, `MAX_ATTACHMENT_MB`, `ALLOWED_ATTACHMENT_EXTENSIONS` | Upload limits | 10, 25, see defaults |
| `REMINDER_*_DAYS`, `REVIEW_CYCLE_MONTHS`, `REVIEW_NOTICE_DAYS_BEFORE` | Workflow timing | see defaults |
| `SCHEDULER_ENABLED` | Run background jobs in this instance | `true` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry | set by template |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Too many sign-in requests" (HTTP 429) | Rate limit hit for the address or network | Wait an hour, or an Admin can clear rows in `rate_limit_counters`. |
| "Invalid or missing CSRF token" | Page left open past the session, or cookies blocked | Reload the page and retry. |
| Sign-in email never arrives | Sender domain not trusted by the recipient mail gateway | See section 7; check Admin → Notifications for the delivery status. |
| App fails to start after deploy | Migration error or missing setting | `az webapp log tail`; the startup script prints each step. Check `DATABASE_URL` and `AZURE_STORAGE_CONNECTION_STRING` are present in app settings. |
| `/health` reports `database` error | PostgreSQL firewall or password | Check the firewall rule and the `DATABASE_URL` setting. |
