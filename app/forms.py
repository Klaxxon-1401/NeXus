"""Focus-scoped WTForms used by server-rendered routes.

All POST forms are CSRF-protected by Flask-WTF. WTForms handles structural
validation; domain services repeat critical checks because HTTP handlers must
never be the only line of defence.
"""

from __future__ import annotations

from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from .constants import NETWORK_CHOICES, PLAN_CHOICES
from .services.payments import validate_wallet_address
from .services.errors import ServiceError
from .utils import is_valid_referral_code, normalize_referral_code, parse_money_to_cents


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _money_range(minimum: int | None = None, maximum: int | None = None):
    """Validate a currency input and attach exact integer cents to the field."""

    def validate(form, field) -> None:
        try:
            cents = parse_money_to_cents(field.data)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if minimum is not None and cents < minimum:
            raise ValidationError(f"Minimum amount is ${minimum / 100:,.2f}.")
        if maximum is not None and cents > maximum:
            raise ValidationError(f"Maximum amount is ${maximum / 100:,.2f}.")
        field.cents = cents

    return validate


class SignupForm(FlaskForm):
    full_name = StringField(
        "Full name",
        filters=[_strip],
        validators=[DataRequired(), Length(min=2, max=80)],
    )
    email = StringField(
        "Email address",
        filters=[_strip],
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(
                min=12,
                max=128,
                message="Use 12–128 characters for your password.",
            ),
        ],
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords do not match."),
        ],
    )
    workspace_name = StringField(
        "Workspace name",
        filters=[_strip],
        validators=[Length(max=60)],
    )
    referral_code = StringField(
        "Invite code",
        filters=[_strip],
        validators=[Length(max=16)],
    )
    accept_terms = BooleanField(
        "I understand this is an educational prototype, not a financial product",
        validators=[DataRequired(message="Please acknowledge the prototype notice.")],
    )
    submit = SubmitField("Create account")

    def validate_referral_code(self, field) -> None:
        if field.data and not is_valid_referral_code(normalize_referral_code(field.data)):
            raise ValidationError("Enter a valid invite code.")

    def validate(self, extra_validators=None) -> bool:
        # Validate the declared fields first, then enforce the conditional rule:
        # a workspace name is required only for self-service workspace creation.
        # Invited members join the inviter's existing workspace.
        valid = super().validate(extra_validators=extra_validators)
        if not self.referral_code.data and not self.workspace_name.data:
            self.workspace_name.errors.append(
                "Choose a workspace name, or use an invite code."
            )
            valid = False
        return valid


class LoginForm(FlaskForm):
    email = StringField(
        "Email address",
        filters=[_strip],
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=1, max=128)],
    )
    submit = SubmitField("Sign in")


class DepositForm(FlaskForm):
    amount = StringField(
        "Amount",
        filters=[_strip],
        validators=[DataRequired()],
    )
    network = SelectField(
        "Network",
        choices=NETWORK_CHOICES,
        validators=[DataRequired()],
    )
    source_address = StringField(
        "Sending wallet address",
        filters=[_strip],
        validators=[DataRequired(), Length(min=20, max=128)],
    )
    tx_hash = StringField(
        "Transaction hash (optional)",
        filters=[_strip],
        validators=[Length(max=128)],
    )
    submit = SubmitField("Create deposit request")

    def validate_amount(self, field) -> None:
        _money_range(
            minimum=current_app.config["DEPOSIT_MIN_CENTS"],
            maximum=current_app.config["DEPOSIT_MAX_CENTS"],
        )(self, field)

    def validate_source_address(self, field) -> None:
        try:
            validate_wallet_address(field.data, self.network.data)
        except ServiceError as exc:
            raise ValidationError(exc.message) from exc


class WithdrawalForm(FlaskForm):
    amount = StringField(
        "Amount",
        filters=[_strip],
        validators=[DataRequired()],
    )
    network = SelectField(
        "Network",
        choices=NETWORK_CHOICES,
        validators=[DataRequired()],
    )
    destination_address = StringField(
        "Destination wallet address",
        filters=[_strip],
        validators=[DataRequired(), Length(min=20, max=128)],
    )
    acknowledge = BooleanField(
        "I understand this is a simulated withdrawal request",
        validators=[DataRequired(message="Please acknowledge the simulation.")],
    )
    submit = SubmitField("Request withdrawal")

    def validate_amount(self, field) -> None:
        _money_range(
            minimum=current_app.config["WITHDRAWAL_MIN_CENTS"],
            maximum=current_app.config["WITHDRAWAL_MAX_CENTS"],
        )(self, field)

    def validate_destination_address(self, field) -> None:
        try:
            validate_wallet_address(field.data, self.network.data)
        except ServiceError as exc:
            raise ValidationError(exc.message) from exc


class InvestmentForm(FlaskForm):
    plan_code = SelectField(
        "Plan",
        choices=PLAN_CHOICES,
        validators=[DataRequired()],
    )
    amount = StringField(
        "Amount",
        filters=[_strip],
        validators=[DataRequired()],
    )
    acknowledge = BooleanField(
        "I understand returns are simulated and not guaranteed",
        validators=[DataRequired(message="Please acknowledge the simulation.")],
    )
    submit = SubmitField("Open position")

    def validate_amount(self, field) -> None:
        _money_range(maximum=5_000_000_00)(self, field)


class ConfirmActionForm(FlaskForm):
    """An empty CSRF-protected form for state-changing buttons."""

    submit = SubmitField("Confirm")
