"""Flask CLI commands for local setup and scheduled return settlement."""

from __future__ import annotations

from datetime import datetime

import click
from flask import current_app
from flask.cli import with_appcontext

from .extensions import db
from .seed import (
    create_demo_operations,
    ensure_demo_accounts,
    ensure_demo_operations,
    seed_demo,
)
from .services.investments import credit_due_returns


@click.command("init-db")
@click.option("--seed", is_flag=True, help="Create the fictional Meridian Circle demo workspace.")
@with_appcontext
def init_db_command(seed: bool) -> None:
    """Create tables for a fresh local SQLite database.

    Production deployments should run versioned Alembic migrations instead;
    this command exists to keep the prototype easy to start.
    """
    db.create_all()
    click.echo("Database tables are ready.")
    if seed:
        created = seed_demo()
        if created:
            click.echo("Demo workspace seeded. Sign in with alex@sigma.demo.")
        else:
            click.echo("Demo workspace already exists; top-up check running.")
        # Repairs databases seeded by older releases (e.g. a missing admin
        # account) without touching existing balances or transactions.
        result = ensure_demo_accounts()
        if result["created"] or result["repaired"]:
            click.echo(
                "Demo accounts created: "
                f"{', '.join(result['created']) or 'none'}; repaired: "
                f"{', '.join(result['repaired']) or 'none'}."
            )
        operations = ensure_demo_operations()
        if operations["created"]:
            click.echo(
                "Demo operations created: " + ", ".join(operations["created"]) + "."
            )


@click.command("ensure-demo")
@with_appcontext
def ensure_demo_command() -> None:
    """Create or repair demo sign-ins and reviewable Operations samples."""
    if current_app.config.get("ENV_NAME") == "production":
        raise click.ClickException("Refusing to prepare demo data in production.")
    result = ensure_demo_accounts()
    operations = ensure_demo_operations()
    click.echo(
        "Demo accounts ready. Created: "
        f"{', '.join(result['created']) or 'none'}; repaired: "
        f"{', '.join(result['repaired']) or 'none'}."
    )
    click.echo(
        "Demo operations ready. Created: "
        f"{', '.join(operations['created']) or 'none'}."
    )


@click.command("seed-operations")
@with_appcontext
def seed_operations_command() -> None:
    """Add a fresh pair of sample deposit/withdrawal requests for review."""
    if current_app.config.get("ENV_NAME") == "production":
        raise click.ClickException("Refusing to seed demo data in production.")
    result = create_demo_operations()
    click.echo(
        "Demo operations created: "
        f"{', '.join(result['created']) or 'none'}."
    )


@click.command("seed-demo")
@with_appcontext
def seed_demo_command() -> None:
    """Seed the fictional demo workspace."""
    if current_app.config.get("ENV_NAME") == "production":
        raise click.ClickException("Refusing to seed demo data in production.")
    created = seed_demo()
    click.echo(
        "Demo workspace seeded."
        if created
        else "Demo workspace already exists; no changes made."
    )


@click.command("credit-returns")
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Catch up returns due on or before this date (default: today).",
)
@with_appcontext
def credit_returns_command(as_of: datetime | None) -> None:
    """Settle every due monthly return (scheduler entry point)."""
    result = credit_due_returns(as_of=as_of.date() if as_of else None)
    click.echo(
        f"Credited {result.credited_count} return(s), "
        f"${result.credited_cents / 100:,.2f}, "
        f"matured {result.investments_matured} position(s)."
    )


def register_cli(app) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_demo_command)
    app.cli.add_command(ensure_demo_command)
    app.cli.add_command(seed_operations_command)
    app.cli.add_command(credit_returns_command)
