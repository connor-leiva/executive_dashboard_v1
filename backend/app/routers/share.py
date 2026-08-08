"""ULRG L10 Scorecard — read-only public share links for ClickUp embeds (SPEC Part 5.3 / Step 8).

Three surfaces, all resolving the tenant from the token (never the Host header) and NEVER requiring
a login:
  GET /api/v1/share/{token}/scorecard   → the same payload as /ulrg/scorecard
  GET /api/v1/share/{token}/room        → a team room (pending Step 6)
  GET /share/{token}                     → a self-contained, no-nav HTML page that renders the
                                           scorecard read-only, carrying the CSP that lets ClickUp
                                           (and only ClickUp / same-origin) frame it.

A revoked or expired token returns 404, not 403, so a dead link leaks nothing (SPEC 5.3). The
CSP `frame-ancestors 'self' https://*.clickup.com` is set on the share PAGE only; the main app is
left untouched (its routes never send it).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Business, ShareLink
from ..services import scorecard

# JSON API — mounted under /api/v1 (see main.py); no auth, token-scoped.
router = APIRouter(prefix="/share", tags=["share"])
# The embeddable HTML page — mounted at the root so ClickUp embeds a clean /share/{token} URL.
page_router = APIRouter(tags=["share"])

_CSP = "frame-ancestors 'self' https://*.clickup.com"


async def _resolve(s: AsyncSession, token: str) -> ShareLink:
    """A live share link, or 404. Revoked/expired both read as 'not found' so a dead link is silent."""
    link = (await s.execute(select(ShareLink).where(ShareLink.token == token))).scalar_one_or_none()
    now = dt.datetime.now(dt.timezone.utc)
    if link is None or link.revoked_at is not None or (link.expires_at is not None and link.expires_at <= now):
        raise HTTPException(404, "Not found")
    return link


async def _ulrg_business(s: AsyncSession, tenant_id) -> Business:
    b = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Not found")
    return b


@router.get("/{token}/scorecard")
async def shared_scorecard(token: str, response: Response, weeks: int = 13,
                           s: AsyncSession = Depends(get_session)):
    link = await _resolve(s, token)
    b = await _ulrg_business(s, link.tenant_id)
    weeks = max(1, min(52, weeks))
    # never cache the payload: the token is in the URL, and a cached copy could outlive a revoke
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return await scorecard.build_scorecard(s, link.tenant_id, b.id, weeks)


@router.get("/{token}/room")
async def shared_room(token: str, s: AsyncSession = Depends(get_session)):
    await _resolve(s, token)                              # validate the token even while unimplemented
    raise HTTPException(404, "Not found")                 # Team Rooms are Step 6; no room payload yet


@page_router.get("/share/{token}", response_class=HTMLResponse)
async def share_page(token: str, s: AsyncSession = Depends(get_session)):
    """The embeddable read-only page. Renders client-side from /api/v1/share/{token}/scorecard so no
    scorecard math is duplicated here. No nav, no period selector, no drill, no link back into the app."""
    await _resolve(s, token)                              # 404 a dead token before serving any shell
    html = _PAGE.replace("__TOKEN__", token)
    return HTMLResponse(html, headers={
        "Content-Security-Policy": _CSP,                  # let ClickUp frame this page (share route only)
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    })


# ── the self-contained page (inline CSS+JS; fetches the token payload and renders read-only) ──────
_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>ULRG L10 Scorecard</title>
<style>
  :root{--parch:#FBF7F0;--card:#fff;--ink:#22302B;--slate:#5A6B63;--muted:#93A29A;--hair:#E7E0D4;
        --ever:#2C3E36;--green:#4F6A4D;--greenbg:#E7EFE5;--amber:#8A6414;--amberbg:#FBF0D8;
        --poppy:#B85434;--poppybg:#FBE7E1;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--parch);color:var(--ink);
       font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:1100px;margin:0 auto;padding:18px 16px 40px}
  h1{font-size:18px;margin:0 0 2px;letter-spacing:.2px}
  .sub{color:var(--muted);font-size:12px;margin-bottom:16px}
  .grp{margin:22px 0 6px;font-weight:700;color:var(--ever);font-size:13px;letter-spacing:.04em;
       text-transform:uppercase;display:flex;gap:8px;align-items:baseline}
  .grp .own{font-weight:500;color:var(--slate);text-transform:none;letter-spacing:0}
  .card{background:var(--card);border:1px solid var(--hair);border-radius:12px;overflow:hidden}
  .scroll{overflow-x:auto}
  table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
  th,td{padding:8px 10px;text-align:center;white-space:nowrap;border-bottom:1px solid var(--hair)}
  th{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}
  td.m{text-align:left;font-weight:600}
  td.note{text-align:left;color:var(--muted);font-size:11px}
  tr:last-child td{border-bottom:none}
  .pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:700}
  .att{font-weight:700}
  .g{color:var(--green)} .gb{background:var(--greenbg)}
  .a{color:var(--amber)} .ab{background:var(--amberbg)}
  .r{color:var(--poppy)} .rb{background:var(--poppybg)}
  .dim{color:var(--muted)}
  .foot{color:var(--muted);font-size:11px;margin-top:18px;text-align:center}
  .err{padding:40px;text-align:center;color:var(--slate)}
</style></head>
<body><div class="wrap" id="root"><div class="err">Loading…</div></div>
<script>
const TOKEN="__TOKEN__";
const band=p=>p==null?null:(p>=100?"g":p>=80?"a":"r");
const bandbg=p=>p==null?"":(p>=100?"gb":p>=80?"ab":"rb");
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const isPct=r=>r.type==="rate"||(r.goal>0&&r.goal<=1);
function fmt(r,v){ if(v==null||v==="")return "–"; return isPct(r)?(Math.round(v*10)/10)+"%":v; }
function verdictPill(c){
  if(!c||c.verdict==null) return "";
  const v=c.verdict, cls=v==="ahead"?"g gb":v==="catchable"?"a ab":v==="stretch"?"a ab":"r rb";
  const label={ahead:"AHEAD",catchable:"CATCHABLE",stretch:"STRETCH",reset:"RESET"}[v]||v.toUpperCase();
  const need=(v!=="ahead"&&c.required!=null)?(c.type==="rate"?Math.round(c.required)+"% ":Math.round(c.required)+"/wk "):"";
  return '<span class="dim" style="font-size:11px">'+need+'</span><span class="pill '+cls+'">'+label+'</span>';
}
async function main(){
  let d;
  try{ const res=await fetch("/api/v1/share/"+TOKEN+"/scorecard?weeks=13");
       if(!res.ok) throw 0; d=await res.json(); }
  catch(e){ document.getElementById("root").innerHTML='<div class="err">This link is no longer available.</div>'; return; }
  const weeks=(d.weeks||[]).slice().reverse();          // newest first
  const shown=weeks.slice(0,6);
  const wkey="w"+(d.default_window||13);
  let h='<h1>L10 Scorecard</h1><div class="sub">'+esc((d.quarter&&d.quarter.key)||"")+
        ' · week '+esc(d.current_week||"")+' newest · read-only</div>';
  for(const g of (d.groups||[])){
    h+='<div class="grp">'+esc(g.name)+(g.owner&&g.owner.name?'<span class="own">'+esc(g.owner.name)+'</span>':'')+'</div>';
    h+='<div class="card scroll"><table><thead><tr>'+
       '<th class="m">Measurable</th><th>Own</th><th>Goal</th><th>13-wk</th><th>Attain</th><th>Gap</th><th>To recover</th>';
    for(const w of shown) h+='<th>'+esc(w.label)+'</th>';
    h+='</tr></thead><tbody>';
    for(const r of (g.rows||[])){
      const c=(r.cumulative&&r.cumulative[wkey]&&r.cumulative[wkey].attain!=null)?r.cumulative[wkey]:null;
      const rate=r.type==="rate";
      h+='<tr><td class="m">'+esc(r.measurable)+(r.type==="snapshot"?' <span class="dim">· snapshot</span>':'')+'</td>';
      h+='<td class="dim">'+esc(r.owner&&r.owner.initials||"")+'</td>';
      h+='<td class="dim">≥'+esc(r.goal)+(isPct(r)?"%":"")+'</td>';
      if(c){
        h+='<td class="dim">'+(rate?(Math.round(c.actual*10)/10)+"% avg":c.actual+" of "+c.target)+'</td>';
        h+='<td class="att '+band(c.attain)+'">'+Math.round(c.attain)+'%</td>';
        h+='<td class="'+(c.gap>=0?"g":"r")+'">'+(c.gap>=0?"+":"")+(rate?(Math.round(c.gap*10)/10)+" pt":c.gap)+'</td>';
        h+='<td>'+verdictPill({...c,type:r.type})+'</td>';
      } else { h+='<td class="dim">–</td><td class="dim">–</td><td class="dim">–</td><td class="dim">not cumulative</td>'; }
      const vals=(r.values||[]).slice().reverse();
      for(let i=0;i<shown.length;i++){
        const v=vals[i]; const pct=(v==null||!r.goal)?null:(v/r.goal*100);
        h+='<td class="'+bandbg(pct)+' '+(band(pct)||"dim")+'">'+fmt(r,v)+'</td>';
      }
      h+='</tr>';
    }
    h+='</tbody></table></div>';
  }
  h+='<div class="foot">Live from Sisu · at or above 100% green, 80–99% amber, under 80% red</div>';
  document.getElementById("root").innerHTML=h;
}
main();
</script></body></html>
"""
