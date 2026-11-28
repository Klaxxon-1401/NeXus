"""WSGI entry point for Gunicorn/uWSGI-style servers."""

from app import create_app

app = create_app()

# Self-heal the hosted demo sign-ins on every boot. Databases seeded by an
# older release may be missing accounts added later (the admin was one), and
# the one-shot seed command returns early once the workspace exists. This is
# idempotent, never touches balances or ledger rows, and is a no-op outside
# DEMO_MODE. Failures are logged rather than blocking startup, so a database
# that has not been migrated yet still surfaces its real error to requests.
if app.config.get("DEMO_MODE"):
    with app.app_context():
        try:
            from app.seed import ensure_demo_accounts, ensure_demo_operations

            # Ensure demo accounts (admin, etc.) exist in the database.
            acct_result = ensure_demo_accounts()
            if acct_result["created"] or acct_result["repaired"]:
                app.logger.info(
                    "demo_accounts_ensured created=%s repaired=%s",
                    ",".join(acct_result["created"]) or "none",
                    ",".join(acct_result["repaired"]) or "none",
                )

            # Ensure the Operations queue has sample pending requests so the
            # admin panel always has something to review, even on databases
            # seeded by older releases.
            ops_result = ensure_demo_operations()
            if ops_result["created"]:
                app.logger.info(
                    "demo_operations_ensured created=%s",
                    ",".join(ops_result["created"]) or "none",
                )
        except Exception:  # pragma: no cover - depends on live database state
            app.logger.exception("demo_accounts_ensure_failed")

if __name__ == "__main__":
    app.run()
