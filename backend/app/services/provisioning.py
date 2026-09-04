"""Tenant provisioning — the ONE path that brings a tenant into existence.

Both `scripts.create_tenant` (the CLI, and the seam a future self-serve signup calls) and
`app.seed` (Spring's fixture) go through here, so a tenant can never again be created
missing a catalog the application assumes is present. Before this existed, only seed.py
wired the catalogs, and a tenant made by the CLI came up with an empty Binder and no chart
of accounts — the app didn't error, it just quietly had nothing to work with.

Two kinds of catalog, and the distinction matters:

  PLATFORM  product data shared by every tenant — the Binder jurisdiction rules (rows with
            tenant_id IS NULL) and the AI skill definitions. Seeded once per DEPLOYMENT;
            calling per tenant is harmless because both are idempotent upserts.
  TENANT    rows this tenant owns — today just the standard chart of accounts, which is
            tenant-scoped precisely so a tenant can edit it.

Deliberately NOT here: `books_scan.seed_ic_rules`. Those three rows are Spring's own CFO
placeholders ("ULRG-Sympli co-op marketing"), not a generic starting point — seeding them
for every tenant would ship one customer's intercompany policy to the next.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from .intranet_bootstrap import bootstrap_intranet
from ..models import Business, Domain, Tenant, User
from ..security import new_action_token
from ..tenancy import RESERVED_SLUGS, url_scheme

INVITE_VALID_DAYS = 7

# Accents handed out in order when the caller does not name one. Every business defaulting to
# the same colour gave a new tenant a rail of identical dots — the dot exists to tie a tile to
# its section, so one colour for everything makes it decoration. Drawn from the product palette
# and ordered so adjacent businesses are easy to tell apart.
DEFAULT_ACCENTS = [
    ("#61835E", "#4D6A4D"),      # meadow
    ("#227175", "#1B5457"),      # teal
    ("#FFBA9F", "#6D5336"),      # petal
    ("#FA8069", "#7A2F1E"),      # poppy
    ("#FFDD1F", "#6D5336"),      # daffodil
    ("#B26248", "#5C3325"),      # terracotta
    ("#C9D3CE", "#42504A"),      # mist
]

# What a tenant gets when the caller doesn't describe its businesses. One generic profit
# center: enough for the dashboard to render, and renamed in Settings rather than in code.
#
# `kind` matters more than it looks — it is how integrations find which business to attach to
# (services/integrations_view.CONNECTABLE_KIND) and how drill-downs find their tab
# (services/tabs.kind_tabs). A tenant with only this one business can connect Sisu and Follow
# Up Boss; Arive and the membership sources need a business of their own role, which is why the
# console lets an operator declare more than one.
DEFAULT_BUSINESSES = [
    {"key": "main", "name": "Main", "tag": "Business", "accent": "#61835E", "ink": "#4D6A4D"},
]


@dataclass
class Provisioned:
    tenant_id: uuid.UUID
    slug: str
    hostname: str
    owner_email: str
    invite_url: str
    catalogs: dict


async def seed_platform_catalogs(s: AsyncSession) -> dict:
    """Product data shared across tenants. Idempotent; does NOT commit."""
    from .ai_skills import seed_ai_skills
    from .binder_rules import seed_jurisdiction_rules

    rules = await seed_jurisdiction_rules(s)
    skills = await seed_ai_skills(s)
    return {"jurisdiction_rules": rules, "ai_skills": skills}


async def seed_tenant_catalogs(s: AsyncSession, tenant_id: uuid.UUID,
                               actor_user_id: uuid.UUID | None = None) -> dict:
    """Per-tenant starting data. Idempotent. `seed_standard_chart` commits internally."""
    from .coa import seed_standard_chart

    chart = await seed_standard_chart(s, tenant_id, actor_user_id)
    return {"standard_chart": chart}


def tenant_hostname(slug: str) -> str:
    return f"{slug}.{settings.PLATFORM_DOMAIN}"


def invite_url(hostname: str, raw_token: str) -> str:
    return f"{url_scheme(hostname)}://{hostname}/accept-invite?token={raw_token}"


def normalize_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("Slug is required")
    if slug in RESERVED_SLUGS:
        raise ValueError(f"'{slug}' is a reserved slug")
    return slug


async def provision_tenant(
    s: AsyncSession,
    *,
    slug: str,
    name: str,
    owner_email: str,
    owner_name: str | None = None,
    businesses: list[dict] | None = None,
    hostname: str | None = None,
    seed_catalogs: bool = True,
) -> Provisioned:
    """Create a tenant, its primary domain, its businesses, and an INVITED owner.

    Returns the owner's one-time invite URL — the only moment the raw token exists. The
    caller owns the transaction for everything except the chart of accounts, which commits
    on its own (see seed_standard_chart); provisioning therefore commits before seeding so
    a half-built tenant can never be left behind by a later failure.
    """
    slug = normalize_slug(slug)
    owner_email = owner_email.strip().lower()
    host = (hostname or tenant_hostname(slug)).lower()

    if (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none():
        raise ValueError(f"Tenant '{slug}' already exists")
    if (await s.execute(select(Domain).where(Domain.hostname == host))).scalar_one_or_none():
        raise ValueError(f"Host '{host}' is already claimed")

    tenant = Tenant(slug=slug, name=name)
    s.add(tenant)
    await s.flush()

    s.add(Domain(tenant_id=tenant.id, hostname=host, is_primary=True))

    for i, b in enumerate(businesses or DEFAULT_BUSINESSES):
        accent, ink = DEFAULT_ACCENTS[i % len(DEFAULT_ACCENTS)]
        s.add(Business(
            tenant_id=tenant.id, key=b["key"], name=b.get("name") or b["key"],
            tag=b.get("tag") or "Business", accent=b.get("accent") or accent,
            ink=b.get("ink") or ink, is_jv=bool(b.get("is_jv")),
            jv_share=Decimal(str(b.get("jv_share", 1.0))),
            kind=b.get("kind") or "real_estate", sort_order=i,
            # Carried through, not dropped. `config` holds program_tabs — the documented way a
            # tenant declares its own programmes instead of inheriting the first customer's —
            # and display_tab routes a financial entity onto its own page. Both were accepted by
            # the caller, silently discarded here, and then absent with no error to explain it.
            config=b.get("config") or None, display_tab=b.get("display_tab") or None))

    raw, token_hash = new_action_token()
    s.add(User(
        tenant_id=tenant.id, email=owner_email,
        name=owner_name or owner_email.split("@")[0],
        password_hash=None, role="owner", status="invited", token_version=0,
        action_token_hash=token_hash, action_token_purpose="invite",
        action_token_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=INVITE_VALID_DAYS)))

    # A workspace nobody can administer is not a provisioned workspace. This creates the roles,
    # capabilities, console_access grant and owner membership the console requires -- generic
    # structure only, never another customer's roster or content.
    await bootstrap_intranet(s, tenant.id, workspace_name=name, subdomain=slug,
                             owner_email=owner_email)

    catalogs: dict = {}
    if seed_catalogs:
        catalogs |= await seed_platform_catalogs(s)
    await s.commit()
    if seed_catalogs:
        catalogs |= await seed_tenant_catalogs(s, tenant.id)

    return Provisioned(tenant_id=tenant.id, slug=slug, hostname=host,
                       owner_email=owner_email, invite_url=invite_url(host, raw),
                       catalogs=catalogs)
