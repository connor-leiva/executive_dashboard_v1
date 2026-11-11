/* Ads is five screens, not one long page. The data hooks stay: a number only
   renders when it belongs to the account and period currently on screen. */
import { useEffect, useState } from "react";

import { API_BASE, getJSON } from "../api";
import { ProductIcon, iconFor } from "../brand/productIcons.jsx";
import { adsCss } from "./adsStyles.js";
import { C, FONT, HEAD } from "./adsTokens.js";
import DrillPanel from "./DrillPanel.jsx";
import Activity from "./screens/Activity.jsx";
import Budget from "./screens/Budget.jsx";
import Performance from "./screens/Performance.jsx";
import SettingsScreen from "./screens/Settings.jsx";
import Today from "./screens/Today.jsx";
import { sampleAdsDrill } from "./sampleAds.js";
import { Btn, Card, Empty, ErrorNote, SCREENS } from "./ui.jsx";
import { qs, useAdsAccounts, useAdsCreatives, useAdsOverview, useAdsWrites } from "./useAds.js";

const PERIODS = [
  { k: "7d", label: "7d" }, { k: "30d", label: "30d" }, { k: "60d", label: "60d" },
  { k: "90d", label: "90d" }, { k: "mtd", label: "MTD" },
];

export default function AdsView() {
  const [screen, setScreen] = useState("today");
  const [period, setPeriod] = useState("30d");
  const [basis, setBasis] = useState("cohort");
  const [account, setAccount] = useState(null);
  const [campaign, setCampaign] = useState(null);
  const [brand, setBrand] = useState(null);
  const [brandTouched, setBrandTouched] = useState(false);
  const [drill, setDrill] = useState({ stage: null, label: null, data: null, loading: false, error: null });

  const accounts = useAdsAccounts();
  const { data, error, loading, retry } = useAdsOverview({ account, period, basis, campaign });
  const creatives = useAdsCreatives({ account, period, campaign, sort: "spend", limit: 48 });
  const writes = useAdsWrites(data?.account?.id);

  useEffect(() => {
    if (brandTouched || !data?.groups?.length) return;
    const first = data.groups.find((g) => g.name !== "Other") || data.groups[0];
    setBrand(first.name);
  }, [data, brandTouched]);

  async function openDrill(ladder, key, label) {
    const stage = `${ladder?.title || "funnel"}:${key}`;
    if (drill.stage === stage) {
      setDrill({ stage: null, label: null, data: null, loading: false, error: null });
      return;
    }
    const rung = (ladder?.rungs || []).find((r) => r.key === key);
    if (rung?.people) {
      setDrill({
        stage, label, loading: false, error: null,
        data: {
          title: label, count: rung.n || 0, subtitle: rung.note || `${rung.n} in this window`,
          columns: ["name", "email", "reached", "detail"], rows: rung.people || [],
        },
      });
      return;
    }
    setDrill({ stage, label, data: null, loading: true, error: null });
    if (!API_BASE) {
      setDrill({ stage, label, data: sampleAdsDrill(key, label), loading: false, error: null });
      return;
    }
    try {
      const d = await getJSON(`/ads/drill/funnel.${key}?${qs({ account, period, basis, campaign })}`);
      setDrill((cur) => (cur.stage === stage ? { ...cur, data: d, loading: false } : cur));
    } catch (e) {
      setDrill((cur) => (cur.stage === stage ? { ...cur, error: e, loading: false } : cur));
    }
  }

  if (error) {
    return <div style={{ padding: 20 }}><ErrorNote onRetry={retry}>Couldn&apos;t load ad performance.</ErrorNote></div>;
  }
  if (loading || !data) {
    return <div style={{ padding: 20 }}><Empty>Loading…</Empty></div>;
  }
  if (!data.connected) {
    return (
      <Card>
        <h2 style={{ fontFamily: HEAD, fontSize: 18, margin: 0, color: C.ink }}>Connect a Meta ad account</h2>
        <p style={{ fontFamily: FONT, fontSize: 14, color: C.slate, maxWidth: 560, lineHeight: 1.6 }}>{data.reason}</p>
        <p style={{ fontFamily: FONT, fontSize: 13, color: C.muted, maxWidth: 560, lineHeight: 1.6 }}>
          You&apos;ll need a System User token with ads_read and ads_management. Until the token includes ads_management, Approve only previews a change.
        </p>
      </Card>
    );
  }

  const acctList = Array.isArray(accounts.data) ? accounts.data : [];

  return (
    <div className="adsx" style={{ padding: "8px 4px 32px", display: "grid", gap: 16 }}>
      <style>{adsCss()}</style>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <ProductIcon name={iconFor("ads")} size={22} tone={C.accent} />
          <div>
            <div style={{ fontFamily: HEAD, fontSize: 18, fontWeight: 600, color: C.ink }}>Ads</div>
            <div style={{ fontFamily: FONT, fontSize: 12, color: C.muted }}>{data.account?.name} · {data.range?.label}</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {PERIODS.map((p) => (
            <Btn key={p.k} primary={period === p.k} onClick={() => setPeriod(p.k)}>{p.label}</Btn>
          ))}
          <Btn primary={basis === "cohort"} onClick={() => setBasis("cohort")}>Cohort</Btn>
          <Btn primary={basis === "period"} onClick={() => setBasis("period")}>Period</Btn>
          {acctList.length > 1 && (
            <select value={account || ""} aria-label="Ad account"
                    onChange={(e) => { setAccount(e.target.value || null); setCampaign(null); }}
                    style={{ fontFamily: FONT, fontSize: 13, borderRadius: 8, border: `1px solid ${C.line}`, padding: "6px 8px" }}>
              {acctList.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
        </div>
      </div>

      <nav aria-label="Ads screens" style={{ display: "flex", gap: 6, flexWrap: "wrap", borderBottom: `1px solid ${C.line}`, paddingBottom: 8 }}>
        {SCREENS.map((s) => (
          <Btn key={s.id} primary={screen === s.id} onClick={() => setScreen(s.id)}>{s.label}</Btn>
        ))}
      </nav>

      {screen === "today" && <Today data={data} onSaved={retry} />}
      {screen === "performance" && (
        <Performance data={data} creatives={creatives.data} brand={brand}
                     setBrand={(name) => { setBrand(name); setBrandTouched(true); }} onDrill={openDrill} />
      )}
      {screen === "budget" && <Budget data={data} onSaved={retry} />}
      {screen === "activity" && (
        <Activity log={writes} accountId={data.account?.id} canUndo={!!data.writes?.can_undo}
                  onSaved={() => { retry(); writes.retry?.(); }} />
      )}
      {screen === "settings" && <SettingsScreen data={data} account={account} onSaved={retry} />}

      {drill.stage && (
        <DrillPanel drill={drill.data} loading={drill.loading} error={drill.error} label={drill.label}
                    onClose={() => setDrill({ stage: null, label: null, data: null, loading: false, error: null })} />
      )}
    </div>
  );
}
