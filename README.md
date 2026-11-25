# Sigma Investments

A full-stack **educational fintech prototype**: a dark, editorial investment
workspace with wallet funding, simulated monthly returns, and a five-level
referral commission engine. The application is intentionally complete enough to
demonstrate real product mechanics while refusing to pretend that it handles
real money.

> **Prototype only.** No real deposits, payouts, securities, or investment
> advice are involved. All people, balances, rates, and returns are fictional.
> Do not deploy this as a financial product without legal, compliance,
> licensing, and provider due diligence.

---

## What is included

- Public landing page, signup, login, and a responsive authenticated workspace.
- Multi-tenant data isolation by workspace.
- Wallet deposit and withdrawal workflows with pending, completed, rejected,
  and refund states.
- Three illustrative plans with monthly return schedules and maturity.
- A scheduler entry point that catches up due monthly returns idempotently.
- Five-level referral commissions: **2.25% / 1.25% / 0.75% / 0.50% / 0.25%**.
- Immutable transaction ledger, referral commission audit table, and activity
  filters with pagination.
- Security baseline: scrypt password hashing, signed HTTP-only sessions, CSRF,
  CSP, security headers, rate limits, strict validation, and tenant-scoped
  queries.
- A seeded demo workspace with a working referral chain, historical positions,
  returns, and pending wallet requests.
- Dependency-free server-rendered charts and visual design; no CDN or external
  font requests.
- A pytest suite covering auth, wallet invariants, returns, referrals,
  cross-tenant access, configuration, and utilities.

## Demo

After seeding, sign in with:

| Account | Role | Email | Password |
| --- | --- | --- | --- |
| Alex Morgan | Workspace owner | `alex@sigma.demo` | `SigmaDemo!2026` |
| Maya Chen | Investor · direct invite | `maya@sigma.demo` | `SigmaDemo!2026` |
| Priya Shah | Investor · second level | `priya@sigma.demo` | `SigmaDemo!2026` |

All eight seeded users share the same demo password. The seeded workspace is
**Meridian Circle** (`meridian-circle`). The referral chain is:

```
Alex → Maya → Priya → Leo → Noor → Elena → Sam
  └── Jonas (direct to Alex)
```

Alex also has a pending deposit and a pending withdrawal so the demo-review UI
is visible immediately. The demo password comes from `DEMO_PASSWORD` and can be
overridden before seeding.

## Quick start

Requirements: Python **3.11+**, pip, and a terminal. SQLite is embedded, so no
database server is needed for local development.

```bash
cd sigma-investments

python -m venv .venv
source .venv/bin/activate          # bash/zsh
# fish: source .venv/bin/activate.fish
# Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

cp .env.example .env                # optional; defaults work out of the box

flask --app run.py init-db --seed
flask --app run.py run --debug
```

Open <http://127.0.0.1:5000>.

The local database is `instance/sigma.db`. To rebuild the demo from scratch:

```bash
rm -f instance/sigma.db
flask --app run.py init-db --seed
```

`init-db` uses `db.create_all()` for prototype convenience. Production should
use versioned migrations (see below).

### Useful commands

```bash
# Run the test suite
pytest -q

# Coverage report
pytest --cov=app --cov-report=term-missing

# Seed only (refuses to run in production)
flask --app run.py seed-demo

# Catch up all monthly returns due today
flask --app run.py credit-returns

# Replay a specific date, e.g. for a scheduler backfill
flask --app run.py credit-returns --as-of 2026-11-30
```

## Configuration

Configuration comes from environment variables, optionally loaded from `.env`.
See `.env.example` for the full list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Selects `development`, `testing`, or `production` |
| `SECRET_KEY` | dev placeholder | Session signing key; production requires 32+ characters |
| `DATABASE_URL` | SQLite | SQLAlchemy URI; PostgreSQL in production |
| `DEMO_MODE` | `true` | Enables destructive simulation endpoints |
| `DEMO_PASSWORD` | `SigmaDemo!2026` | Password used by seeded users |
| `SESSION_HOURS` | `8` | Absolute session lifetime |
| `SESSION_COOKIE_SECURE` | `false` | Require HTTPS for the session cookie |
| `TRUST_PROXY_HEADERS` | `false` | Honor `X-Forwarded-*` from a trusted proxy |
| `FORCE_HTTPS` | `false` | Redirect insecure requests |
| `RATELIMIT_STORAGE_URI` | `memory://` | Use Redis for multi-process deployments |
| `RATELIMIT_DEFAULT` | `300 per hour` | Global default limit |

Development and testing defaults are intentionally convenient. The production
config **fails closed** at startup unless it has a strong `SECRET_KEY`, a
`DATABASE_URL`, `DEMO_MODE` disabled, and proxy trust enabled.

## Architecture

```text
sigma-investments/
├── app/
│   ├── __init__.py          # app factory, hooks, template globals, errors
│   ├── config.py            # environment configs + production validation
│   ├── constants.py         # plans, rates, networks, labels
│   ├── extensions.py        # SQLAlchemy, CSRF, limiter instances
│   ├── forms.py             # WTForms validation
│   ├── security.py          # sessions, login guard, CSP and headers
│   ├── seed.py              # fictional Meridian Circle workspace
│   ├── cli.py               # init-db, seed-demo, credit-returns
│   ├── models/              # Tenant, User, Wallet, Investment, Transaction, Referral
│   ├── services/            # auth, ledger, wallet, investments, referrals, payments
│   ├── routes/              # public, auth, dashboard, portfolio, wallet, referrals, activity
│   ├── templates/           # Jinja layouts + pages + macros
│   └── static/              # local CSS, JS, fonts, logo
├── docs/architecture.md     # request path, ERD, state machines, integration seams
├── tests/                   # pytest suite and factories
├── run.py                   # local Flask entry point
├── wsgi.py                  # Gunicorn entry point
├── requirements*.txt
└── .env.example
```

### Request and data flow

1. Flask loads the session and re-validates the user, tenant, active status, and
   absolute session age on every request.
2. Routes enforce authentication, CSRF, rate limits, and form validation.
3. Domain services perform wallet and ledger mutations inside a single database
   transaction.
4. Jinja renders server-side HTML. JavaScript is progressive enhancement only;
   every form works without it.

Detailed diagrams and design decisions are in
[`docs/architecture.md`](docs/architecture.md).

### Tenant model

A tenant is a client workspace and the isolation boundary for all financial
data. Self-service signup creates a new tenant and an `owner`. Signup with a
valid referral code joins the inviter's tenant as an `investor` and creates one
referral edge. This keeps each referral tree and its commissions inside a single
ledger.

Every wallet, investment, transaction, referral, and commission row carries a
`tenant_id`. Personal queries filter by both tenant and user. Cross-tenant IDs
return "not found" instead of revealing that a record exists.

## Financial mechanics

### Integer money and basis points

All balances are integer **cents**. Rates are integer **basis points**
(1 bps = 0.01%). The helper in `app/utils.py` performs half-up rounding:

```python
def basis_points(amount_cents: int, rate_bps: int) -> int:
    return (amount_cents * rate_bps + 5_000) // 10_000
```

This avoids floating-point drift in ledgers and commission splits.

### Plans

| Plan | Minimum | Term | Monthly rate | Annualised display |
| --- | --- | --- | --- | --- |
| Foundation | $100 | 6 months | 0.75% | 9.00% |
| Meridian | $1,000 | 12 months | 1.25% | 15.00% |
| Summit | $5,000 | 24 months | 1.75% | 21.00% |

Monthly return:

```
monthly_return_cents = basis_points(principal_cents, monthly_rate_bps)
```

A $2,500 Meridian position credits **$31.25** per month for 12 months, then
matures. Rates are illustrative; the annualised figure is a simple display
multiple, not a compounding promise.

### Monthly settlement

Each position stores `months_credited`, `next_credit_on`, and `matures_on`.
`credit_due_returns()` selects active positions that are due, credits one month
at a time, advances the cursor, and marks the position matured after the final
instalment. Every credit has an idempotency key
`return:<investment_id>:<month_number>`, so a retried job cannot double-pay.
Late jobs catch up across missed months.

In production, call the CLI from a scheduler:

```cron
# Every hour; the command is idempotent and catches up missed periods.
0 * * * * cd /srv/sigma && .venv/bin/flask --app run.py credit-returns >> /var/log/sigma-returns.log 2>&1
```

For larger deployments, use Celery beat, Cloud Scheduler, or a queue worker and
call the same service function with a database advisory lock per tenant.

### Referral commissions

Commissions are paid **once per new position**, at opening, on the investor's
principal:

```
commission_cents = basis_points(principal_cents, level_rate_bps)
```

| Level | Relationship | Rate | $10,000 position |
| --- | --- | --- | --- |
| 1 | Direct referrer | 2.25% | $225.00 |
| 2 | Their referrer | 1.25% | $125.00 |
| 3 | Third degree | 0.75% | $75.00 |
| 4 | Fourth degree | 0.50% | $50.00 |
| 5 | Fifth degree | 0.25% | $25.00 |
| | **Total** | **5.00%** | **$500.00** |

The platform funds commissions; the investor's principal is not reduced. The
service walks `referrals` edges upward with cycle protection and a hard
five-level cap. Each payout has a unique key
`referral:<investment_id>:<level>:<beneficiary_id>` and an immutable
`referral_commissions` row linked to its ledger transaction.

### Wallet lifecycle

- **Deposit:** creates a pending transaction. The balance changes only when a
  provider confirms it. Duplicate transaction hashes are rejected per tenant.
- **Withdrawal:** checks available cash, reserves it immediately, and creates a
  pending transaction. Approval settles it; rejection writes a separate
  `withdrawal_refund` credit so the statement remains auditable.
- **Demo controls:** confirmation, approval, rejection, and early return
  settlement exist only while `DEMO_MODE=true`.

## Security notes

- Passwords use Werkzeug **scrypt** with per-password salts and encoded
  parameters for future upgrades. Minimum length is 12 characters.
- Authentication uses Flask's signed session cookie, not a JWT in
  `localStorage`. Cookies are HTTP-only, SameSite=Lax, and Secure in production.
  Login clears the prior session to prevent fixation; an absolute server-side
  lifetime is enforced on each request.
- **CSRF** tokens protect every mutating form; expired tokens render a dedicated
  recovery page.
- **CSP** is strict: `script-src 'self'`, `style-src 'self'`, no inline scripts,
  styles, or event handlers. Fonts and scripts are served locally.
- Additional headers: frame denial, `nosniff`, referrer policy, permissions
  policy, COOP, and HSTS on HTTPS responses.
- **Rate limits** cover login, signup, deposits, withdrawals, investments, and
  demo transitions. Use Redis in production.
- Inputs are validated by WTForms and re-validated in services (amount limits,
  supported networks, address formats, plan codes, ownership).
- Session identities are re-checked against the database on every request;
  suspended users or tenants are logged out.
- Demo routes return 404 when `DEMO_MODE` is disabled. Seeding refuses to run
  outside demo mode.
- Production startup fails if critical settings are unsafe.

### Production deployment sketch

```bash
python -m pip install -r requirements.txt -r requirements-postgres.txt
export APP_ENV=production
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://sigma:password@db:5432/sigma"
export DEMO_MODE=false
export TRUST_PROXY_HEADERS=true
export FORCE_HTTPS=true
export SESSION_COOKIE_SECURE=true
export RATELIMIT_STORAGE_URI="redis://redis:6379/0"

gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app
```

Put Gunicorn behind a TLS-terminating proxy that overwrites
`X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host`. Only then enable
`TRUST_PROXY_HEADERS`. Terminate TLS with a valid certificate; HSTS is emitted
on secure responses.

Before going live:

- Replace `db.create_all()` with **Alembic/Flask-Migrate** migrations and review
  them.
- Move rate-limit storage to Redis and sessions to a shared store if you run
  multiple processes.
- Add structured logging, error tracking, uptime checks, and database backups.
- Add email verification, password reset, MFA, and account recovery.
- Add a real audit log for administrative actions and immutable exportable
  statements.
- Reconcile every completed transaction against provider settlement reports.
- Complete legal, KYC/AML, sanctions, tax, and consumer-protection review.
- Never seed demo data or enable simulation endpoints in production.

## Payment and external services

No Stripe, PayPal, or custody provider is integrated. The deliberate seam is
`app/services/payments.py`:

- `PaymentProviderStub.create_deposit_intent(...)` — replace with a hosted
  checkout or custody deposit-address request.
- `PaymentProviderStub.create_payout(...)` — replace with an idempotent payout
  call.
- `validate_wallet_address`, `validate_source_address`,
  `validate_destination_address`, and `validate_tx_hash` perform format checks
  only. A real integration must verify webhook signatures and on-chain/provider
  settlement.

Recommended production flow:

1. Create a provider intent and persist its provider ID on the pending
   transaction.
2. Receive a signed webhook.
3. Verify the signature and replay window.
4. Call the idempotent wallet service using the provider event ID as the
   idempotency key.
5. Reconcile the ledger against a provider settlement report.

The current demo confirmation routes simulate steps 2–4. They are not webhook
handlers and must not be exposed in production.

## Tests

```bash
pytest -q
```

The suite uses an in-memory SQLite database, CSRF disabled, and rate limiting
disabled. It covers:

- signup, invite-code joining, duplicate email, login failure/success, open
  redirect protection, session requirements;
- deposit limits, duplicate hashes, withdrawal reservation/refund/approval;
- cross-tenant object access attempts;
- five-level commission distribution, cap, idempotency, and wallet effects;
- return catch-up, no double payment, maturity, and per-user portfolio isolation;
- security headers, CSP, CSRF rejection, production fail-closed configuration;
- money parsing, half-up basis points, date clamping, masking, and chart helpers.

## Prototype limitations

- Simulated returns and referral percentages are fictional illustrations.
- No real payment provider, webhook verification, KYC, AML, or custody.
- No email, password reset, MFA, or notification system.
- No administrative back office; demo review actions are per-user simulations.
- SQLite is used locally; `db.create_all()` is not a migration strategy.
- In-memory rate limits are per process.
- No market pricing, secondary transfers, or tax reporting.

See [`docs/architecture.md`](docs/architecture.md) for request diagrams, the
data model, state machines, and integration boundaries.
