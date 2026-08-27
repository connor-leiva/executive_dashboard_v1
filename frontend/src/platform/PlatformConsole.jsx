/* The operator console: provision and look after customer workspaces, without a terminal.
 *
 * It shows OPERATIONAL HEALTH and never business data — the API is built that way (see
 * routers/platform.py _tenant_row) and this UI cannot ask for more than it returns. An operator
 * can see that a customer's Sisu feed has been broken for a week; they cannot see the customer's
 * revenue.
 *
 * Served at admin.<PLATFORM_DOMAIN>, and ONLY there. It used to live at /platform inside the
 * tenant bundle, because the wildcard DNS that makes admin.* reachable did not exist yet — a
 * console nobody can reach is worth less than one behind a separate origin. The wildcard exists
 * now, so the compromise can go: auth.jsx renders this only on the operator host, and does not
 * register the route at all on a tenant host.
 *
 * Nothing here is a security boundary — the operator API requires a `pu` token that no tenant
 * session can produce (backend deps.current_platform_user), and every check runs server-side.
 * The host split is about not confusing a human, and about not shipping this code to customers.
 *
 * Everything is GREYSCALE on purpose. See OPS in theme.js: the palette is what tells an operator
 * which building they are standing in, on a screen whose buttons switch off paying customers.
 */
import { useCallback, useEffect, useState } from "react";

import { OPS, alpha, relativeTime } from "../theme.js";
import {
  clearOpSession, createTenant, getTenant, hasOpToken, listTenants, opLogin, opName,
  resendInvite, resumeTenant, suspendTenant, tenantAudit,
} from "./api.js";

const FONT = "Inter,sans-serif";
const HEAD = "Poppins,sans-serif";

const card = { background: OPS.white, border: `1px solid ${OPS.line}`, borderRadius: 12, padding: 20 };
const label = {
  fontFamily: FONT, fontSize: 11, fontWeight: 600, letterSpacing: ".07em",
  textTransform: "uppercase", color: OPS.muted,
};
const input = {
  width: "100%", boxSizing: "border-box", padding: "10px 12px", borderRadius: 8,
  border: `1px solid ${OPS.line}`, fontFamily: FONT, fontSize: 14, color: OPS.ink,
  background: OPS.white, outline: "none",
};

function Button({ children, onClick, kind = "primary", disabled, type = "button" }) {
  const kinds = {
    primary: { background: OPS.evergreen, color: OPS.onDark, border: `1px solid ${OPS.evergreen}` },
    quiet: { background: OPS.white, color: OPS.slate, border: `1px solid ${OPS.line}` },
    danger: { background: OPS.white, color: OPS.poppyText, border: `1px solid ${alpha(OPS.poppy, 0.5)}` },
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      style={{
        ...kinds[kind], padding: "9px 14px", borderRadius: 8, fontFamily: FONT,
        fontSize: 13, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
      }}>
      {children}
    </button>
  );
}

function Notice({ error, children }) {
  if (!children) return null;
  return (
    <div style={{
      background: error ? alpha(OPS.poppy, 0.09) : OPS.meadowBg,
      border: `1px solid ${error ? alpha(OPS.poppy, 0.35) : OPS.sprout}`,
      color: error ? OPS.poppyText : OPS.meadowInk,
      borderRadius: 8, padding: "10px 12px", fontFamily: FONT, fontSize: 13, marginBottom: 14,
    }}>{children}</div>
  );
}

/* A one-time invite link is the only moment the raw token exists — it cannot be read back out of
   the database afterwards, only reissued. So it gets a copy button rather than a line of text
   somebody has to select by hand and might truncate. */
function InviteLink({ url }) {
  const [copied, setCopied] = useState(false);
  if (!url) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={label}>One-time invite link</div>
      <div style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "stretch" }}>
        <code style={{
          ...input, fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace", fontSize: 12,
          overflowX: "auto", whiteSpace: "nowrap", display: "block", lineHeight: "22px",
        }}>{url}</code>
        <Button kind="quiet" onClick={() => {
          navigator.clipboard.writeText(url).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          });
        }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <div style={{ fontFamily: FONT, fontSize: 12, color: OPS.tertiary, marginTop: 6 }}>
        Send this to the owner. It is shown once — if it is lost, reissue it from the workspace.
      </div>
    </div>
  );
}

function StatusDot({ status, errors, syncFailures }) {
  const bad = status === "suspended";
  const warn = !bad && ((errors || 0) > 0 || (syncFailures || 0) > 0);
  const dot = bad ? OPS.poppy : warn ? OPS.daffodil : OPS.meadow;
  const text = bad ? "Suspended" : warn ? "Needs attention" : "Healthy";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6,
                   fontFamily: FONT, fontSize: 12.5, color: OPS.tertiary }}>
      <span style={{ width: 8, height: 8, borderRadius: 4, background: dot }} />{text}
    </span>
  );
}

function Stat({ k, v }) {
  return (
    <div>
      <div style={label}>{k}</div>
      <div style={{ fontFamily: FONT, fontSize: 14, color: OPS.ink, marginTop: 5,
                    wordBreak: "break-word" }}>{v}</div>
    </div>
  );
}

/* ── operator sign-in ─────────────────────────────────────────────────────────── */
function OperatorLogin({ onDone }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await opLogin(email, password);
      onDone();
    } catch (ex) {
      // The status is kept rather than flattened. The tenant login collapses every failure into
      // "check your email and password", which is how a tenant-resolution outage reached a human
      // as a mystery instead of as an error. A locked account and a wrong password are different
      // problems and the person signing in can act on the difference.
      setErr(ex.status === 423 || ex.status === 0
        ? ex.message
        : "That email and password did not match an operator account.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", background: OPS.parchment, display: "flex",
                  alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onSubmit={submit} style={{ ...card, width: 380, maxWidth: "100%" }}>
        <div style={{ fontFamily: HEAD, fontSize: 19, fontWeight: 600, color: OPS.ink }}>
          Operator console
        </div>
        <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.tertiary, margin: "6px 0 18px" }}>
          Acumyn staff only. This is not a customer login.
        </div>
        <Notice error>{err}</Notice>
        <div style={{ marginBottom: 12 }}>
          <div style={label}>Email</div>
          <input style={{ ...input, marginTop: 6 }} type="email" value={email} required
                 autoComplete="username" onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div style={{ marginBottom: 18 }}>
          <div style={label}>Password</div>
          <input style={{ ...input, marginTop: 6 }} type="password" value={password} required
                 autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
        </div>
        <Button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
      </form>
    </div>
  );
}

/* ── new workspace ────────────────────────────────────────────────────────────── */

/* What a business DOES, which is how the product finds it — integrations attach by role
   (services/integrations_view.CONNECTABLE_KIND) and drill-downs resolve their tab by role
   (services/tabs.kind_tabs). Not cosmetic: a workspace with no membership business has
   nowhere to attach Go High Level, and its card will say exactly that. */
const KINDS = [
  { value: "real_estate", label: "Real estate", tag: "Real estate",
    hint: "Transactions, agents, GCI. Sisu and Follow Up Boss attach here." },
  { value: "commission_jv", label: "Lending / JV", tag: "Lending",
    hint: "Paid per closing, with its own split. Arive attaches here." },
  { value: "membership", label: "Memberships", tag: "Membership",
    hint: "Programmes and cohorts sold as memberships. Go High Level and Stripe attach here." },
  { value: "holding", label: "Holding", tag: "Holding",
    hint: "Books and compliance only - no operational feed." },
];

const slugify = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_")
  .replace(/^_+|_+$/g, "").slice(0, 24);

function NewWorkspace({ onCreated, onCancel }) {
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [name, setName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [hostname, setHostname] = useState("");
  // At least one line. The API would default to a single generic business, but then the
  // operator never learns the choice exists - and a workspace whose only business is
  // real-estate silently cannot connect the membership or lending sources.
  const [lines, setLines] = useState([{ name: "", kind: "real_estate" }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  // Derive the id from the company name until the operator types one themselves, so the common
  // case needs two fields rather than four.
  const effectiveSlug = slugTouched
    ? slug
    : name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 24);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      // Named lines only, key derived from the name and de-duplicated. An empty list means
      // "you did not say", and the API falls back to one generic business.
      const seen = new Set();
      const businesses = lines
        .filter((l) => l.name.trim())
        .map((l, i) => {
          let key = slugify(l.name) || `biz_${i + 1}`;
          while (seen.has(key)) key = `${key}_${seen.size + 1}`;
          seen.add(key);
          const meta = KINDS.find((k) => k.value === l.kind);
          return { key, name: l.name.trim(), kind: l.kind, tag: meta ? meta.tag : "Business" };
        });
      const r = await createTenant({
        slug: effectiveSlug,
        name: name.trim(),
        owner_email: ownerEmail.trim(),
        hostname: hostname.trim() || null,
        businesses: businesses.length ? businesses : null,
      });
      setResult(r);
      onCreated();
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div style={{ ...card, marginBottom: 20 }}>
        <div style={{ fontFamily: HEAD, fontSize: 16, fontWeight: 600, color: OPS.ink }}>
          {result.slug} is live
        </div>
        <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.tertiary, marginTop: 6 }}>
          Reachable at <strong>{result.hostname}</strong>. Owner invited: {result.owner_email}.
          Its businesses and catalogs are seeded and ready.
        </div>
        <InviteLink url={result.invite_url} />
        <div style={{ marginTop: 16 }}><Button kind="quiet" onClick={onCancel}>Done</Button></div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} style={{ ...card, marginBottom: 20 }}>
      <div style={{ fontFamily: HEAD, fontSize: 16, fontWeight: 600, color: OPS.ink,
                    marginBottom: 14 }}>
        New workspace
      </div>
      <Notice error>{err}</Notice>
      <div style={{ display: "grid", gap: 12,
                    gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
        <div>
          <div style={label}>Company name</div>
          <input style={{ ...input, marginTop: 6 }} value={name} required
                 placeholder="Acme Realty" onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <div style={label}>Owner email</div>
          <input style={{ ...input, marginTop: 6 }} type="email" value={ownerEmail} required
                 placeholder="owner@acme.com" onChange={(e) => setOwnerEmail(e.target.value)} />
        </div>
        <div>
          <div style={label}>Workspace ID</div>
          <input style={{ ...input, marginTop: 6 }} value={effectiveSlug} required
                 onChange={(e) => { setSlugTouched(true); setSlug(e.target.value); }} />
          <div style={{ fontFamily: FONT, fontSize: 12, color: OPS.tertiary, marginTop: 5 }}>
            Used in their web address. Lowercase letters and numbers.
          </div>
        </div>
        <div>
          <div style={label}>Web address (optional)</div>
          <input style={{ ...input, marginTop: 6 }} value={hostname}
                 placeholder="leave blank for the default"
                 onChange={(e) => setHostname(e.target.value)} />
          <div style={{ fontFamily: FONT, fontSize: 12, color: OPS.tertiary, marginTop: 5 }}>
            Set this only if they bring their own domain. It must already point at the app, and it
            is where every link sent to them gets built from.
          </div>
        </div>
      </div>

      <div style={{ marginTop: 22, paddingTop: 18, borderTop: `1px solid ${OPS.line}` }}>
        <div style={label}>Their businesses</div>
        <div style={{ fontFamily: FONT, fontSize: 12, color: OPS.tertiary, margin: "5px 0 10px" }}>
          Each one is a profit centre on their dashboard. What it DOES decides which data sources
          can attach to it, so a workspace with no lending business has nowhere to connect Arive.
          They can rename these later; the role is the part worth getting right now.
        </div>
        {lines.map((l, i) => {
          const meta = KINDS.find((k) => k.value === l.kind);
          const set = (patch) => setLines(lines.map((x, j) => (j === i ? { ...x, ...patch } : x)));
          return (
            <div key={i} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input style={{ ...input, flex: 2 }} value={l.name}
                       placeholder={i === 0 ? "Acme Realty" : "Another business"}
                       onChange={(e) => set({ name: e.target.value })} />
                <select style={{ ...input, flex: 1 }} value={l.kind}
                        onChange={(e) => set({ kind: e.target.value })}>
                  {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                </select>
                <button type="button" aria-label="Remove this business"
                        onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : lines)}
                        disabled={lines.length === 1}
                        style={{ background: "none", border: "none", cursor: lines.length === 1 ? "default" : "pointer",
                                 color: lines.length === 1 ? OPS.sprout : OPS.muted, fontSize: 18, padding: "0 4px" }}>
                  &times;
                </button>
              </div>
              {meta && (
                <div style={{ fontFamily: FONT, fontSize: 11.5, color: OPS.muted, marginTop: 4 }}>
                  {meta.hint}
                </div>
              )}
            </div>
          );
        })}
        <button type="button" onClick={() => setLines([...lines, { name: "", kind: "membership" }])}
                style={{ background: "none", border: `1px dashed ${OPS.line}`, borderRadius: 8,
                         padding: "7px 12px", cursor: "pointer", fontFamily: FONT, fontSize: 12.5,
                         color: OPS.slate }}>
          + Add another business
        </button>
        <div style={{ fontFamily: FONT, fontSize: 11.5, color: OPS.muted, marginTop: 8 }}>
          Leave all blank and they get one generic business, which can connect Sisu and Follow Up
          Boss but nothing else until they add more.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
        <Button type="submit" disabled={busy || !name || !ownerEmail || !effectiveSlug}>
          {busy ? "Creating…" : "Create workspace"}
        </Button>
        <Button kind="quiet" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

/* ── one workspace ────────────────────────────────────────────────────────────── */
function WorkspaceDetail({ slug, onBack, onChanged }) {
  const [row, setRow] = useState(null);
  const [events, setEvents] = useState([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [invite, setInvite] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRow(await getTenant(slug));
      setEvents((await tenantAudit(slug)).events || []);
    } catch (ex) {
      setErr(ex.message);
    }
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  async function act(fn, msg) {
    setBusy(true);
    setErr("");
    setNote("");
    try {
      const r = await fn(slug);
      if (r && r.invite_url) setInvite(r.invite_url);
      setNote(msg);
      await load();
      onChanged();
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  if (!row) {
    return (
      <div style={card}>
        <Notice error>{err}</Notice>
        <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.tertiary }}>
          {err ? "" : "Loading…"}
        </div>
        <div style={{ marginTop: 12 }}><Button kind="quiet" onClick={onBack}>Back</Button></div>
      </div>
    );
  }

  const suspended = row.status === "suspended";
  return (
    <div>
      <button onClick={onBack} style={{
        background: "none", border: "none", padding: 0, marginBottom: 14, cursor: "pointer",
        fontFamily: FONT, fontSize: 13, fontWeight: 600, color: OPS.slate,
      }}>← All workspaces</button>

      <div style={{ ...card, marginBottom: 16 }}>
        <Notice error>{err}</Notice>
        <Notice>{note}</Notice>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16,
                      flexWrap: "wrap" }}>
          <div>
            <div style={{ fontFamily: HEAD, fontSize: 20, fontWeight: 600, color: OPS.ink }}>
              {row.name}
            </div>
            <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.tertiary, marginTop: 4 }}>
              {row.slug} ·{" "}
              <StatusDot status={row.status} errors={row.sources_in_error}
                         syncFailures={row.sync_failures_7d} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
            {row.users_pending_invite > 0 && (
              <Button kind="quiet" disabled={busy}
                      onClick={() => act(resendInvite, "A fresh invite link was issued.")}>
                Reissue owner invite
              </Button>
            )}
            {suspended
              ? <Button disabled={busy}
                        onClick={() => act(resumeTenant, "Workspace resumed.")}>Resume</Button>
              : <Button kind="danger" disabled={busy}
                        onClick={() => act(suspendTenant,
                          "Workspace suspended. Everyone signed in was signed out.")}>
                  Suspend
                </Button>}
          </div>
        </div>
        <InviteLink url={invite} />

        <div style={{ display: "grid", gap: 14, marginTop: 20,
                      gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
          <Stat k="Web addresses" v={(row.hosts || []).join(", ") || "none"} />
          <Stat k="People" v={`${row.users}${row.users_pending_invite
            ? ` (${row.users_pending_invite} not yet accepted)` : ""}`} />
          <Stat k="Last sign-in" v={relativeTime(row.last_login_at) || "never"} />
          <Stat k="Businesses" v={row.businesses} />
          <Stat k="Connected sources" v={`${row.sources}${row.sources_in_error
            ? ` · ${row.sources_in_error} failing` : ""}`} />
          <Stat k="Last sync" v={relativeTime(row.last_synced_at) || "never"} />
        </div>

        {(row.errors || []).length > 0 && (
          <div style={{ marginTop: 18 }}>
            <div style={label}>Sources needing attention</div>
            {row.errors.map((e, i) => (
              <div key={i} style={{
                marginTop: 8, padding: "9px 11px", borderRadius: 8,
                background: alpha(OPS.poppy, 0.07), border: `1px solid ${alpha(OPS.poppy, 0.28)}`,
                fontFamily: FONT, fontSize: 12.5, color: OPS.ink,
              }}>
                <strong>{e.provider}</strong> — {e.error || "failing"}
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ fontFamily: HEAD, fontSize: 15, fontWeight: 600, color: OPS.ink,
                      marginBottom: 4 }}>
          Recent activity
        </div>
        <div style={{ fontFamily: FONT, fontSize: 12.5, color: OPS.tertiary, marginBottom: 12 }}>
          Who was invited, what was connected, who signed in. Operational history, not their data.
        </div>
        {events.length === 0 && (
          <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.muted }}>Nothing recorded yet.</div>
        )}
        {events.map((e, i) => (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 0",
            borderTop: i ? `1px solid ${OPS.line}` : "none", fontFamily: FONT, fontSize: 13,
          }}>
            <span style={{ color: OPS.ink }}>{e.action}</span>
            <span style={{ color: OPS.muted, whiteSpace: "nowrap" }}>{relativeTime(e.at) || ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── list ─────────────────────────────────────────────────────────────────────── */
function WorkspaceList({ onOpen }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows((await listTenants()).tenants || []);
    } catch (ex) {
      setErr(ex.message);
      setRows([]);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                    marginBottom: 18, gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: HEAD, fontSize: 20, fontWeight: 600, color: OPS.ink }}>
            Workspaces
          </div>
          <div style={{ fontFamily: FONT, fontSize: 13, color: OPS.tertiary, marginTop: 3 }}>
            {rows ? `${rows.length} customer${rows.length === 1 ? "" : "s"}` : "Loading…"}
          </div>
        </div>
        {!creating && <Button onClick={() => setCreating(true)}>New workspace</Button>}
      </div>

      <Notice error>{err}</Notice>
      {creating && <NewWorkspace onCreated={load} onCancel={() => setCreating(false)} />}

      <div style={{ display: "grid", gap: 12 }}>
        {(rows || []).map((r) => (
          <button key={r.slug} onClick={() => onOpen(r.slug)} style={{
            ...card, textAlign: "left", cursor: "pointer", display: "flex",
            justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap",
          }}>
            <div>
              <div style={{ fontFamily: HEAD, fontSize: 15.5, fontWeight: 600, color: OPS.ink }}>
                {r.name}
              </div>
              <div style={{ fontFamily: FONT, fontSize: 12.5, color: OPS.tertiary, marginTop: 4 }}>
                {(r.hosts || [])[0] || "no web address"} · {r.users}{" "}
                {r.users === 1 ? "person" : "people"}
                {r.users_pending_invite ? ` · ${r.users_pending_invite} not yet accepted` : ""}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <StatusDot status={r.status} errors={r.sources_in_error}
                         syncFailures={r.sync_failures_7d} />
              <div style={{ fontFamily: FONT, fontSize: 12, color: OPS.muted, marginTop: 4 }}>
                last sign-in {relativeTime(r.last_login_at) || "never"}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ── shell ────────────────────────────────────────────────────────────────────── */
export default function PlatformConsole() {
  const [authed, setAuthed] = useState(hasOpToken());
  const [slug, setSlug] = useState(null);
  const [tick, setTick] = useState(0);

  if (!authed) return <OperatorLogin onDone={() => setAuthed(true)} />;

  return (
    <div style={{ minHeight: "100vh", background: OPS.parchment, fontFamily: FONT }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "14px 22px", background: OPS.evergreen, gap: 12, flexWrap: "wrap",
      }}>
        <span style={{ fontFamily: HEAD, fontSize: 15, fontWeight: 600, color: OPS.onDark }}>
          Acumyn · Operator console
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 12.5, color: OPS.onDarkMute }}>{opName()}</span>
          <button onClick={() => { clearOpSession(); setAuthed(false); }} style={{
            background: "none", border: `1px solid ${alpha(OPS.onDark, 0.35)}`, borderRadius: 7,
            padding: "5px 11px", color: OPS.onDark, fontFamily: FONT, fontSize: 12.5,
            fontWeight: 600, cursor: "pointer",
          }}>Sign out</button>
        </span>
      </div>
      <div style={{ maxWidth: 940, margin: "0 auto", padding: "26px 20px 60px" }}>
        {slug
          ? <WorkspaceDetail key={`${slug}-${tick}`} slug={slug} onBack={() => setSlug(null)}
                             onChanged={() => setTick((n) => n + 1)} />
          : <WorkspaceList key={tick} onOpen={setSlug} />}
      </div>
    </div>
  );
}
