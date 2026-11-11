/* Ads is five screens, not one long page. The data hooks stay: a number only
   renders when it belongs to the account and period currently on screen. */
import { useEffect, useState } from "react";

import { API_BASE, getJSON } from "../api";
import { ProductIcon, iconFor } from "../brand/productIcons.jsx";
import { C } from "./adsTokens.js";
import DrillPanel from "./DrillPanel.jsx";
import Activity from "./screens/Activity.jsx";
import Budget from "./screens/Budget.jsx";
import Performance from "./screens/Performance.jsx";
import SettingsScreen from "./screens/Settings.jsx";
import Today from "./screens/Today.jsx";
import { sampleAdsDrill } from "./sampleAds.js";
import { screenCss } from "./screenStyles.js";
import { Card, Empty, ErrorNote, SCREENS, Segmented } from "./ui.jsx";
import { qs, useAdsAccounts, useAdsCreatives, useAdsOverview, useAdsWrites } from "./useAds.js";

export default function AdsView({ periodKey }) {
  const [screen, setScreen] = useState("today");
  const [period, setPeriod] = useState(periodKey || "mtd");
  const [basis, setBasis] = useState("cohort");
  const [account, setAccount] = useState(null);
  const [campaign, setCampaign] = useState(null);
  const [brand, setBrand] = useState(null);
  const [drill, setDrill] = useState({ stage: null, label: null, data: null, loading: false, error: null });

  const accounts = useAdsAccounts();
  const { data, error, loading, retry } = useAdsOverview({ account, period, basis, campaign });
  const creatives = useAdsCreatives({ account, period, campaign, sort: "spend", limit: 48 });
  const writes = useAdsWrites(data?.account?.id);

  useEffect(() => {
    if (periodKey) setPeriod(periodKey);
  }, [periodKey]);

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
    return <Frame><ErrorNote onRetry={retry}>Couldn&apos;t load ad performance.</ErrorNote></Frame>;
  }
  if (loading || !data) {
    return <Frame><Empty>Loading…</Empty></Frame>;
  }
  if (!data.connected) {
    return (
      <Frame>
        <Card title="Connect a Meta ad account">
          <div className="ax-stack" style={{ gap: 8 }}>
            <p className="ax-text">{data.reason}</p>
            <p className="ax-text">
              You&apos;ll need a System User token with ads_read and ads_management. Until the token includes ads_management, Approve only previews a change.
            </p>
          </div>
        </Card>
      </Frame>
    );
  }

  const acctList = Array.isArray(accounts.data) ? accounts.data : [];

  return (
    <Frame>
      <div className="ax-top">
        <div className="ax-brand">
          <ProductIcon name={iconFor("ads")} size={24} tone={C.accent} />
          <div>
            <h1 className="ax-h1">Ads</h1>
            <div className="ax-meta">{data.account?.name} · {data.range?.label}</div>
          </div>
        </div>
        <div className="ax-row">
          {screen !== "settings" && (
            <Segmented label="How this window is counted" value={basis} onChange={setBasis}
                       options={[["cohort", "Cohort"], ["period", "Period"]]} />
          )}
          {acctList.length > 1 && (
            <select className="ax-input" value={account || ""} aria-label="Ad account"
                    onChange={(e) => { setAccount(e.target.value || null); setCampaign(null); }}
                    style={{ width: "auto" }}>
              {acctList.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
        </div>
      </div>

      <nav aria-label="Ads screens" className="ax-tabs">
        {SCREENS.map((s) => (
          <button key={s.id} type="button" className="ax-tab" onClick={() => setScreen(s.id)}
                  aria-current={screen === s.id ? "page" : undefined}>{s.label}</button>
        ))}
      </nav>

      {screen === "today" && <Today data={data} onSaved={retry} onBudget={() => setScreen("budget")} />}
      {screen === "performance" && (
        <Performance data={data} creatives={creatives.data} brand={brand}
                     setBrand={setBrand} onDrill={openDrill} />
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
    </Frame>
  );
}

/* Every state the tab can be in sits inside the same scope and stylesheet, the loading line and
   the connect card included, so none of them renders in the browser's default type. */
function Frame({ children }) {
  return (
    <div className="adsx">
      <style>{screenCss()}</style>
      {children}
    </div>
  );
}
