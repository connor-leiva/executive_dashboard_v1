"""Axcion charging workspaces, through Axcion's own Stripe account (OPERATOR-CONSOLE-SPEC §6).

NOT a workspace's Stripe. A workspace's own Stripe account is a revenue source it syncs from
(integrations/stripe_legacy.py, stripe_bc.py). This is the opposite direction of money: Axcion's
account, billing the workspace for its plan. Nothing here reads or writes a workspace's Stripe.

WHAT IS AUTHORITATIVE (§6.2). Stripe owns the charged amount, the subscription status, the period,
the payment method and the invoices; the tables written here are mirrors of those and are never
edited by hand. Axcion owns the plan tier, the token budget, the billing contact and the PO
reference. Changing the plan never calls Stripe, and the console says so.

THE ACCOUNT'S KEYS ARE CONFIGURED IN THE CONSOLE, not in environment variables: an operator pastes
them into the System view and they are stored encrypted with FERNET_KEY, exactly as every workspace
integration credential already is. That follows Connor's standing instruction that credentials live
in the product rather than in Railway's variables; the spec's STRIPE_PLATFORM_* settings are
replaced by the platform_billing_config row. Until it is connected and switched on, every route
that would call Stripe refuses, and the reconciliation job does nothing.

MONEY IS CENTS, everywhere, always. Stripe's unit; formatted only at the edge.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (PlatformAudit, PlatformBillingConfig, PlatformInvoice, PlatformStripeEvent,
                      PlatformSubscription, Tenant)
from ..security import dec

STRIPE = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 300
# §6.1: prices are found by lookup_key, so no price id is ever written into the code.
LOOKUP_KEYS = {"team": "price_team_monthly", "business": "price_business_monthly",
               "portfolio": "price_portfolio_monthly"}
# The transitions an operator needs a history of (§6.3).
AUDITED_STATUSES = frozenset({"past_due", "canceled", "incomplete"})
HANDLED_EVENTS = frozenset({
    "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed",
    "invoice.finalized", "payment_method.attached", "payment_method.detached",
})


class BillingUnavailable(Exception):
    """Platform billing is not connected, or is switched off."""


class StripeError(Exception):
    """Stripe refused a request. The message is Stripe's own."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _ts(value) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(int(value), dt.timezone.utc) if value else None


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ── configuration ─────────────────────────────────────────────────────────────────────────
async def config(s: AsyncSession) -> PlatformBillingConfig | None:
    return await s.get(PlatformBillingConfig, 1)


async def active_key(s: AsyncSession) -> str:
    """The secret key, when billing is connected and switched on. Raises BillingUnavailable."""
    cfg = await config(s)
    if cfg is None or not cfg.secret_key_enc:
        raise BillingUnavailable("Platform billing is not connected. Connect Axcion's Stripe account "
                                 "on the System page first.")
    if not cfg.enabled:
        raise BillingUnavailable("Platform billing is connected but switched off on the System page.")
    return dec(cfg.secret_key_enc)


def mode_of(key: str) -> str | None:
    """live or test, from the key's own prefix, which is how Stripe marks it."""
    for prefix, mode in (("sk_live_", "live"), ("rk_live_", "live"), ("sk_test_", "test"), ("rk_test_", "test")):
        if key.startswith(prefix):
            return mode
    return None


def dashboard_url(path: str, livemode: bool | None) -> str:
    return f"https://dashboard.stripe.com/{'' if livemode else 'test/'}{path}"


# ── the Stripe API ────────────────────────────────────────────────────────────────────────
async def stripe(key: str, method: str, path: str, data: list | dict | None = None,
                 params: list | dict | None = None) -> dict:
    """One Stripe API call. Form-encoded, as Stripe expects. A refusal raises StripeError carrying
    Stripe's own message, never a generic one."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, f"{STRIPE}{path}", headers={"Authorization": f"Bearer {key}"},
                                data=data, params=params)
    except httpx.HTTPError as e:
        raise StripeError(f"Stripe could not be reached: {type(e).__name__}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        message = ((body.get("error") or {}).get("message")) or f"Stripe answered {r.status_code}."
        raise StripeError(message, r.status_code)
    return body


# ── webhooks ──────────────────────────────────────────────────────────────────────────────
def verify_signature(payload: bytes, header: str | None, secret: str, now: int | None = None) -> bool:
    """Stripe's scheme: `t=<unix>,v1=<hex hmac-sha256 of "t.payload">`, one or more v1 values.

    Checked before the body is parsed, against the raw bytes, with a constant-time comparison and a
    five-minute tolerance so a captured delivery cannot be replayed later.
    """
    if not header or not secret:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


def _method_text(pm) -> tuple[str | None, str | None]:
    """("Visa ending 4242", "09/2028") from an expanded payment method. An unexpanded id says
    nothing a person can read, so it yields nothing."""
    if not isinstance(pm, dict):
        return None, None
    if pm.get("card"):
        card = pm["card"]
        brand = str(card.get("brand") or "card").replace("_", " ").title()
        exp = (f"{int(card['exp_month']):02d}/{card['exp_year']}"
               if card.get("exp_month") and card.get("exp_year") else None)
        return f"{brand} ending {card.get('last4', '????')}", exp
    if pm.get("us_bank_account"):
        bank = pm["us_bank_account"]
        return f"{bank.get('bank_name') or 'Bank account'} ending {bank.get('last4', '????')}", None
    return str(pm.get("type") or "payment method").replace("_", " ").title(), None


async def _tenant_for(s: AsyncSession, obj: dict) -> Tenant | None:
    """The workspace a Stripe object belongs to: its metadata first (set when the console created
    the customer), then the customer id already mirrored."""
    tid = (obj.get("metadata") or {}).get("axcion_tenant_id")
    if tid:
        try:
            tenant = await s.get(Tenant, uuid.UUID(tid))
            if tenant is not None:
                return tenant
        except ValueError:
            pass
    customer = obj.get("customer")
    customer = customer.get("id") if isinstance(customer, dict) else customer
    if not customer:
        return None
    row = (await s.execute(select(PlatformSubscription).where(
        PlatformSubscription.stripe_customer_id == customer))).scalar_one_or_none()
    return await s.get(Tenant, row.tenant_id) if row else None


async def apply_subscription(s: AsyncSession, obj: dict, event_at: dt.datetime | None = None,
                             deleted: bool = False) -> PlatformSubscription | None:
    """Upsert the mirror from a subscription object. Never insert-only: events arrive out of order,
    so an event older than the last one applied to this row is ignored rather than written over
    newer state."""
    tenant = await _tenant_for(s, obj)
    if tenant is None:
        return None
    row = await s.get(PlatformSubscription, tenant.id)
    customer = obj.get("customer")
    customer = customer.get("id") if isinstance(customer, dict) else customer
    if row is None and not customer:
        return None
    if row is None:
        row = PlatformSubscription(tenant_id=tenant.id, stripe_customer_id=customer, status=obj.get("status") or "incomplete")
        s.add(row)
        previous = None
    else:
        previous = row.status
        if event_at is not None and row.stripe_event_at is not None and _aware(row.stripe_event_at) > event_at:
            return row
    items = ((obj.get("items") or {}).get("data") or [])
    price = (items[0].get("price") if items else None) or {}
    recurring = price.get("recurring") or {}
    row.stripe_customer_id = customer or row.stripe_customer_id
    row.stripe_subscription_id = obj.get("id") or row.stripe_subscription_id
    row.stripe_price_id = price.get("id") or row.stripe_price_id
    row.status = "canceled" if deleted else (obj.get("status") or row.status)
    row.amount_cents = price.get("unit_amount") if price.get("unit_amount") is not None else row.amount_cents
    row.currency = (price.get("currency") or row.currency or "usd")[:3]
    row.interval = recurring.get("interval") or row.interval
    row.current_period_end = _ts((items[0].get("current_period_end") if items else None)
                                 or obj.get("current_period_end")) or row.current_period_end
    row.trial_end = _ts(obj.get("trial_end"))
    row.cancel_at = _ts(obj.get("cancel_at"))
    label, exp = _method_text(obj.get("default_payment_method"))
    if label:
        row.default_payment_method, row.payment_method_exp = label, exp
    elif obj.get("default_payment_method") is None and "default_payment_method" in obj:
        row.default_payment_method, row.payment_method_exp = None, None
    row.synced_at = dt.datetime.now(dt.timezone.utc)
    if event_at is not None:
        row.stripe_event_at = event_at
    if row.status != previous and row.status in AUDITED_STATUSES:
        s.add(PlatformAudit(operator_id=None, operator_email=None, action=f"billing.{row.status}",
                            tenant_id=tenant.id, tenant_slug=tenant.slug, target_type="subscription",
                            target_id=row.stripe_subscription_id,
                            detail={"source": "stripe", "from": previous, "to": row.status}))
    return row


async def apply_invoice(s: AsyncSession, obj: dict) -> PlatformInvoice | None:
    """Upsert one invoice, then recompute the lifetime collected total from every mirrored invoice.

    Derived rather than incremented: a replayed invoice.paid rewrites the same row with the same
    amount, and a sum over rows cannot count it twice however many times it arrives.
    """
    tenant = await _tenant_for(s, obj)
    if tenant is None or not obj.get("id"):
        return None
    row = await s.get(PlatformInvoice, obj["id"])
    if row is None:
        row = PlatformInvoice(stripe_invoice_id=obj["id"], tenant_id=tenant.id,
                              created_at=_ts(obj.get("created")) or dt.datetime.now(dt.timezone.utc),
                              status=obj.get("status") or "draft", amount_due_cents=int(obj.get("amount_due") or 0))
        s.add(row)
    row.number = obj.get("number") or row.number
    row.status = obj.get("status") or row.status
    row.amount_due_cents = int(obj.get("amount_due") or 0)
    row.amount_paid_cents = int(obj.get("amount_paid") or 0)
    row.attempt_count = int(obj.get("attempt_count") or 0)
    row.hosted_invoice_url = obj.get("hosted_invoice_url") or row.hosted_invoice_url
    paid_at = ((obj.get("status_transitions") or {}).get("paid_at"))
    row.paid_at = _ts(paid_at) or row.paid_at
    await s.flush()
    sub = await s.get(PlatformSubscription, tenant.id)
    if sub is not None:
        sub.collected_cents = int((await s.execute(
            select(func.coalesce(func.sum(PlatformInvoice.amount_paid_cents), 0))
            .where(PlatformInvoice.tenant_id == tenant.id))).scalar_one())
    return row


async def handle_event(s: AsyncSession, event: dict) -> str:
    """Apply one verified webhook event, once. Returns what happened, for the response body."""
    event_id, kind = event.get("id"), event.get("type")
    if not event_id or not kind:
        return "ignored: not an event"
    if await s.get(PlatformStripeEvent, event_id) is not None:
        return "duplicate"
    obj = ((event.get("data") or {}).get("object")) or {}
    event_at = _ts(event.get("created"))
    if kind.startswith("customer.subscription."):
        await apply_subscription(s, obj, event_at, deleted=kind.endswith(".deleted"))
    elif kind.startswith("invoice."):
        await apply_invoice(s, obj)
    elif kind.startswith("payment_method."):
        # The object is the method itself, with no subscription attached: refresh the customer's
        # subscription so the mirrored "Visa ending 4242" matches what Stripe will charge.
        customer = obj.get("customer")
        row = (await s.execute(select(PlatformSubscription).where(
            PlatformSubscription.stripe_customer_id == customer))).scalar_one_or_none() if customer else None
        if row is not None:
            tenant = await s.get(Tenant, row.tenant_id)
            try:
                await sync_tenant(s, tenant, await active_key(s))
            except (BillingUnavailable, StripeError):
                pass
    s.add(PlatformStripeEvent(id=event_id, type=kind[:64]))
    await s.commit()
    return "applied" if kind in HANDLED_EVENTS else "recorded"


# ── pulling from Stripe ───────────────────────────────────────────────────────────────────
MIRRORED = ("status", "amount_cents", "interval", "current_period_end", "trial_end", "cancel_at",
            "default_payment_method")


async def sync_tenant(s: AsyncSession, tenant: Tenant, key: str) -> list[str]:
    """Pull this workspace's subscription and recent invoices from Stripe and correct the mirror.
    Returns each field it had to change, which the reconciliation job logs as divergence."""
    row = await s.get(PlatformSubscription, tenant.id)
    if row is None or not row.stripe_customer_id:
        return []
    before = {f: getattr(row, f) for f in MIRRORED}
    pulled_at = dt.datetime.now(dt.timezone.utc)
    if row.stripe_subscription_id:
        sub = await stripe(key, "GET", f"/subscriptions/{row.stripe_subscription_id}",
                           params=[("expand[]", "default_payment_method")])
        sub.setdefault("metadata", {}).setdefault("axcion_tenant_id", str(tenant.id))
        # Stamped as of the pull: a webhook created before this moment describes older state and
        # must not overwrite what was just read.
        await apply_subscription(s, sub, pulled_at)
    invoices = await stripe(key, "GET", "/invoices",
                            params=[("customer", row.stripe_customer_id), ("limit", "24")])
    for inv in invoices.get("data") or []:
        inv.setdefault("metadata", {})
        inv["metadata"].setdefault("axcion_tenant_id", str(tenant.id))
        await apply_invoice(s, inv)
    changed = [f for f in MIRRORED if getattr(row, f) != before[f]]
    row.synced_at = dt.datetime.now(dt.timezone.utc)
    await s.commit()
    return changed


def monthly_cents(row: PlatformSubscription) -> int:
    """What one subscription contributes to MRR: active only, a yearly price spread over twelve."""
    if row.status != "active" or row.amount_cents is None:
        return 0
    return round(row.amount_cents / 12) if row.interval == "year" else row.amount_cents
