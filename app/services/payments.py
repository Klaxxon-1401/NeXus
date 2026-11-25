"""Payment-provider boundary and stubs.

No external payment provider is integrated in this prototype. These functions
mark the exact seam where a production provider belongs:

* fiat card / ACH / SEPA: Stripe or Adyen
* crypto deposits and payouts: a custody provider such as Fireblocks,
  Coinbase Prime or BitGo
* webhook verification: provider SDK signature check followed by an
  idempotent ledger mutation

The local flow creates *requests* only. In DEMO_MODE, an authenticated user
can explicitly mark their own request confirmed/approved to exercise the
ledger. That shortcut is disabled automatically in production.
"""

from __future__ import annotations

import re

from flask import current_app

from .errors import ServiceError

EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
BTC_ADDRESS_RE = re.compile(r"^(?:bc1[a-zA-HJ-NP-Z0-9]{25,89}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")
SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TX_HASH_RE = re.compile(r"^(?:0x)?[a-zA-Z0-9]{8,128}$")


def validate_network(network: str) -> str:
    if network not in current_app.config["DEPOSIT_ADDRESSES"]:
        raise ServiceError("Choose a supported payment network.")
    return network


def deposit_address_for(network: str) -> str:
    validate_network(network)
    return current_app.config["DEPOSIT_ADDRESSES"][network]


def validate_wallet_address(address: str, network: str) -> str:
    """Perform format-level checks; on-chain ownership requires a provider."""
    network = validate_network(network)
    address = (address or "").strip()
    patterns = {
        "ethereum": EVM_ADDRESS_RE,
        "bitcoin": BTC_ADDRESS_RE,
        "solana": SOL_ADDRESS_RE,
    }
    if not patterns[network].fullmatch(address):
        raise ServiceError("That wallet address is not valid for the selected network.")
    return address


def validate_tx_hash(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not TX_HASH_RE.fullmatch(cleaned):
        raise ServiceError("The transaction hash format is not valid.")
    return cleaned


def validate_source_address(address: str, network: str) -> str:
    return validate_wallet_address(address, network)


def validate_destination_address(address: str, network: str) -> str:
    return validate_wallet_address(address, network)


class PaymentProviderStub:
    """Documented seam for a real provider client.

    A concrete implementation would create provider-side intents, return
    hosted checkout URLs, and confirm funds only inside a verified webhook.
    """

    def create_deposit_intent(self, *, tenant_id: str, user_id: str, amount_cents: int, network: str):
        raise NotImplementedError(
            "Connect Stripe/Adyen (fiat) or Fireblocks/Coinbase Prime (crypto) here."
        )

    def create_payout(self, *, tenant_id: str, request_id: str, amount_cents: int, address: str):
        raise NotImplementedError(
            "Submit an idempotent payout to the custody/payment provider here."
        )


def demo_mode_enabled() -> bool:
    return bool(current_app.config.get("DEMO_MODE"))
