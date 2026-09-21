import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { exportWtdPlaybook } from "../api.js";
import { COPY, WTD_KIND_LABEL as KIND_LABEL, WTD_SECTIONS as SECTIONS } from "../constants.js";
import {
  useCheckWtdLists,
  useCreateWtdList,
  useCreateWtdScript,
  useDeleteWtdList,
  useDeleteWtdScript,
  useFollowUpSettings,
  useFubSmartLists,
  useImportWtdPlaybook,
  useOrderWtdLists,
  useOrderWtdScripts,
  usePatchFollowUpSettings,
  usePatchWtdList,
  usePatchWtdPerson,
  usePatchWtdPlaybook,
  usePatchWtdScript,
  useWtdLists,
  useWtdPeople,
  useWtdPlaybook,
  useWtdScripts,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* WIN THE DAY, AS THE WORKSPACE WRITES IT (WHOS-WHO-WIN-THE-DAY-SPEC.md, phase 2).
 *
 * The portal's page was a compiled-in checklist; this is where a team writes its own playbook.
 * The sections mirror the portal's tabs, so what an admin edits here is laid out the way agents
 * read it there. Nothing reaches agents until somebody first presses Publish; after that an edit is
 * live on their next page load, as everywhere else in the console. Needs You Today's settings live
 * here too, where they always have. */

/* WHAT A SAVE DOES, said truthfully. Publishing is what first puts something in front of the team;
   after that, like every other content type here, an edit is live on the next page load. Telling
   somebody their change "waits for Publish" when agents already see it would be a small lie that
   costs a lot the day somebody edits the live playbook to try something out. */
function savedText(live) {
  return live
    ? "Saved. It is live: agents see it on their next page load."
    : "Saved as a draft. Publish to put it in front of the team.";
}

function errText(err) {
  return err?.detail || err?.message || COPY.loadFailed;
}

// A key the server accepts (letters, digits, - and _, up to 32) that nothing else will share.
function newKey(prefix) {
  return `${prefix}${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

// ── small editing pieces ──────────────────────────────────────────────────────────────────

function TextField({ label, value, onChange, max, multiline = false, placeholder, rows = 3 }) {
  return (
    <Field label={label}>
      {multiline ? (
        <textarea value={value ?? ""} maxLength={max} rows={rows} placeholder={placeholder}
                  onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input value={value ?? ""} maxLength={max} placeholder={placeholder}
               onChange={(event) => onChange(event.target.value)} />
      )}
    </Field>
  );
}

function NumberField({ label, value, onChange, min = 0, max, placeholder }) {
  return (
    <Field label={label}>
      <input type="number" inputMode="numeric" min={min} max={max} value={value ?? ""}
             placeholder={placeholder}
             onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />
    </Field>
  );
}

/* AN ORDERED LIST OF THINGS THE PAGE DRAWS IN ORDER: blocks, steps, habits, scoreboard rows,
   on-ramp phases, tools. Up, down and remove on each; add at the end. */
function RepeatList({ items, onChange, make, render, addLabel, max, emptyLabel }) {
  const list = items || [];
  const update = (i, patch) => onChange(list.map((item, j) => (j === i ? { ...item, ...patch } : item)));
  const move = (i, to) => {
    if (to < 0 || to >= list.length) return;
    const next = [...list];
    const [moved] = next.splice(i, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };
  return (
    <div className="wtdc-repeat">
      {!list.length && emptyLabel ? <p className="followup-hint">{emptyLabel}</p> : null}
      {list.map((item, i) => (
        <div className="wtdc-item" key={item.key || `${i}`}>
          <div className="wtdc-item-head">
            <strong>{i + 1}</strong>
            <div className="tile-actions">
              <button type="button" disabled={i === 0} onClick={() => move(i, i - 1)}>{COPY.moveUp}</button>
              <button type="button" disabled={i === list.length - 1} onClick={() => move(i, i + 1)}>{COPY.moveDown}</button>
              <button type="button" onClick={() => onChange(list.filter((_, j) => j !== i))}>Remove</button>
            </div>
          </div>
          <div className="wtdc-item-fields">{render(item, (patch) => update(i, patch), i)}</div>
        </div>
      ))}
      {!max || list.length < max ? (
        <div><Button type="button" onClick={() => onChange([...list, make()])}>{addLabel}</Button></div>
      ) : null}
    </div>
  );
}

const TitleText = (item, set, titleLabel = "Title", textLabel = "Text") => (
  <>
    <TextField label={titleLabel} value={item.title} max={160} onChange={(v) => set({ title: v })} />
    <TextField label={textLabel} value={item.text} max={1200} multiline onChange={(v) => set({ text: v })} />
  </>
);

/* ONE OR MORE SECTIONS OF THE DOCUMENT, edited as a local draft and saved whole. The server
   validates each section and names the field it refuses. */
function SectionForm({ title, names, content, intro, live, children }) {
  const save = usePatchWtdPlaybook();
  const pick = () => Object.fromEntries(names.map((n) => [n, clone(content[n])]));
  const [draft, setDraft] = useState(pick);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  // After a save the form takes the server's copy, which is the validated one. ONLY WHEN THESE
  // SECTIONS CHANGED: saving a list card refetches the whole playbook too, and resetting on every
  // refetch threw away whatever somebody was still typing in the form above it.
  const synced = useRef(JSON.stringify(pick()));
  useEffect(() => {
    const now = JSON.stringify(pick());
    if (now !== synced.current) {
      synced.current = now;
      setDraft(pick());
    }
  }, [content]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, patch) => setDraft((d) => ({ ...d, [name]: { ...d[name], ...patch } }));

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await save.mutateAsync(draft);
      setMessage(savedText(live));
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <Panel title={title}>
      <form className="wtdc-form" onSubmit={submit}>
        {intro ? <p className="followup-intro">{intro}</p> : null}
        {children(draft, set)}
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={save.isPending}>Save</Button>
          {message ? <span className="roster-form-message">{message}</span> : null}
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────────────────

function PageSection({ playbook }) {
  const people = playbook.fub?.people_url;
  return (
    <SectionForm title="Page" names={["header", "rule", "tabs"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="The top of Win the Day: what it is, where agents work it, and the one rule the whole page rests on.">
      {(d, set) => (
        <>
          <div className="wtdc-grid-2">
            <TextField label="Eyebrow" value={d.header.eyebrow} max={60} placeholder="Agent Playbook"
                       onChange={(v) => set("header", { eyebrow: v })} />
            <TextField label="Title" value={d.header.title} max={80} placeholder="Win the Day."
                       onChange={(v) => set("header", { title: v })} />
          </div>
          <TextField label="Lede" value={d.header.lede} max={400} multiline rows={2}
                     onChange={(v) => set("header", { lede: v })} />
          <h3 className="wtdc-subhead">Facts under the title (up to three)</h3>
          <RepeatList items={d.header.meta} max={3} addLabel="Add a fact"
                      make={() => ({ k: "", v: "" })}
                      onChange={(meta) => set("header", { meta })}
                      render={(m, setM) => (
                        <div className="wtdc-grid-2">
                          <TextField label="Label" value={m.k} max={40} placeholder="Home Base" onChange={(v) => setM({ k: v })} />
                          <TextField label="Value" value={m.v} max={80} placeholder="Follow Up Boss" onChange={(v) => setM({ v: v })} />
                        </div>
                      )} />
          <div className="wtdc-grid-2">
            <TextField label="Button label" value={d.header.cta.label} max={60}
                       placeholder={people ? "Open Follow Up Boss →" : "No button"}
                       onChange={(v) => set("header", { cta: { ...d.header.cta, label: v } })} />
            <TextField label="Button link" value={d.header.cta.url} max={500}
                       placeholder={people ? `Blank opens ${people}` : "https://"}
                       onChange={(v) => set("header", { cta: { ...d.header.cta, url: v || null } })} />
          </div>
          <h3 className="wtdc-subhead">The one rule</h3>
          <TextField label="Eyebrow" value={d.rule.eyebrow} max={80} placeholder="The One Rule That Matters"
                     onChange={(v) => set("rule", { eyebrow: v })} />
          <TextField label="The rule" value={d.rule.text} max={600} multiline
                     onChange={(v) => set("rule", { text: v })} />
          <TextField label="Under it" value={d.rule.sub} max={240}
                     onChange={(v) => set("rule", { sub: v })} />
          <h3 className="wtdc-subhead">Tab names</h3>
          <p className="followup-hint">{"{n} becomes the number of lists: “The {n} Lists” reads “The 13 Lists”. A tab with nothing on it is not shown."}</p>
          <div className="wtdc-grid-3">
            {[["run", "Today’s run"], ["lists", "Lists"], ["call", "The call"],
              ["scripts", "Scripts"], ["numbers", "Numbers"], ["tools", "Tools"]].map(([key, label]) => (
              <TextField key={key} label={label} value={d.tabs[key]} max={40}
                         onChange={(v) => set("tabs", { [key]: v })} />
            ))}
          </div>
        </>
      )}
    </SectionForm>
  );
}

// ── Today's Run ───────────────────────────────────────────────────────────────────────────

function RunSection({ playbook }) {
  return (
    <SectionForm title="Today’s Run" names={["run"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="The blocks of the day, in order. Each list picks the block that works it (on the Lists tab), and a block shows the numbers of its lists.">
      {(d, set) => (
        <>
          <TextField label="Heading" value={d.run.title} max={120} placeholder="{N} Blocks. Same Shape Every Day."
                     onChange={(v) => set("run", { title: v })} />
          <p className="followup-hint">{"{N} becomes the number of blocks in words (“Five”); {n} in digits."}</p>
          <TextField label="Intro" value={d.run.intro} max={600} multiline rows={2}
                     onChange={(v) => set("run", { intro: v })} />
          <h3 className="wtdc-subhead">Blocks</h3>
          <RepeatList items={d.run.blocks} max={8} addLabel="Add a block"
                      make={() => ({ key: newKey("b"), title: "", minutes: null, text: "" })}
                      onChange={(blocks) => set("run", { blocks })}
                      render={(b, setB) => (
                        <>
                          <div className="wtdc-grid-2">
                            <TextField label="Title" value={b.title} max={60} onChange={(v) => setB({ title: v })} />
                            <NumberField label="Minutes" value={b.minutes} min={1} max={480} onChange={(v) => setB({ minutes: v })} />
                          </div>
                          <TextField label="What happens in it" value={b.text} max={800} multiline onChange={(v) => setB({ text: v })} />
                        </>
                      )} />
          <h3 className="wtdc-subhead">Trips people up</h3>
          <RepeatList items={d.run.trips} max={4} addLabel="Add one" emptyLabel="None yet."
                      make={() => ({ title: "", text: "" })}
                      onChange={(trips) => set("run", { trips })}
                      render={(t, setT) => TitleText(t, setT)} />
          <h3 className="wtdc-subhead">Once a week</h3>
          <TextField label="Eyebrow" value={d.run.weekly.eyebrow} max={120} placeholder="And Once A Week, On Top Of The Day"
                     onChange={(v) => set("run", { weekly: { ...d.run.weekly, eyebrow: v } })} />
          <RepeatList items={d.run.weekly.items} max={4} addLabel="Add one" emptyLabel="None yet."
                      make={() => ({ title: "", text: "" })}
                      onChange={(items) => set("run", { weekly: { ...d.run.weekly, items } })}
                      render={(t, setT) => TitleText(t, setT)} />
        </>
      )}
    </SectionForm>
  );
}

// ── Lists ─────────────────────────────────────────────────────────────────────────────────

function ListsText({ playbook }) {
  return (
    <SectionForm title="The lists page" names={["lists"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="The headings lists sit under, what to watch for, and the shared lead ponds. Save a new heading here before picking it on a list below.">
      {(d, set) => (
        <>
          <TextField label="Heading" value={d.lists.title} max={160} placeholder="Lists 1 Through {n}, in the Order You Run Them."
                     onChange={(v) => set("lists", { title: v })} />
          <TextField label="Intro" value={d.lists.intro} max={600} multiline rows={2}
                     onChange={(v) => set("lists", { intro: v })} />
          <h3 className="wtdc-subhead">Headings</h3>
          <RepeatList items={d.lists.groups} max={12} addLabel="Add a heading"
                      make={() => ({ key: newKey("g"), label: "" })}
                      onChange={(groups) => set("lists", { groups })}
                      render={(g, setG) => (
                        <TextField label="Heading" value={g.label} max={120} placeholder="Time-sensitive — contact today, not tomorrow"
                                   onChange={(v) => setG({ label: v })} />
                      )} />
          <h3 className="wtdc-subhead">Trips people up</h3>
          <RepeatList items={d.lists.trips} max={4} addLabel="Add one" emptyLabel="None yet."
                      make={() => ({ title: "", text: "" })}
                      onChange={(trips) => set("lists", { trips })}
                      render={(t, setT) => TitleText(t, setT)} />
          <h3 className="wtdc-subhead">Lead ponds</h3>
          <TextField label="Eyebrow" value={d.lists.ponds.eyebrow} max={160}
                     onChange={(v) => set("lists", { ponds: { ...d.lists.ponds, eyebrow: v } })} />
          <TextField label="Text" value={d.lists.ponds.text} max={800} multiline rows={2}
                     onChange={(v) => set("lists", { ponds: { ...d.lists.ponds, text: v } })} />
          <RepeatList items={d.lists.ponds.links} max={12} addLabel="Add a pond list" emptyLabel="No pond lists."
                      make={() => ({ label: "", list_id: "" })}
                      onChange={(links) => set("lists", { ponds: { ...d.lists.ponds, links } })}
                      render={(l, setL) => (
                        <div className="wtdc-grid-2">
                          <TextField label="Name" value={l.label} max={80} onChange={(v) => setL({ label: v })} />
                          <TextField label="Smart list id" value={l.list_id} max={12} placeholder="74"
                                     onChange={(v) => setL({ list_id: v.replace(/[^0-9]/g, "") })} />
                        </div>
                      )} />
        </>
      )}
    </SectionForm>
  );
}

function listDraft(list) {
  return {
    name: list.name || "", external_list_id: list.external_list_id || "", cadence: list.cadence || "",
    kind: list.kind || "clear", group_key: list.group_key || "", block_key: list.block_key || "",
    description: list.description || "", script_ids: list.script_ids || [], active: Boolean(list.active),
  };
}

function ListCard({ list, index, total, playbook, scripts, smartLists, busy, onMove, onSaved, onError }) {
  const patch = usePatchWtdList();
  const remove = useDeleteWtdList();
  const [draft, setDraft] = useState(() => listDraft(list));
  useEffect(() => { setDraft(listDraft(list)); }, [list]);
  const set = (field, value) => setDraft((d) => ({ ...d, [field]: value }));
  const base = playbook.fub?.list_base;
  const href = base && draft.external_list_id ? `${base.replace(/\/$/, "")}/${draft.external_list_id}` : "";
  const groups = playbook.content.lists.groups;
  const blocks = playbook.content.run.blocks;

  function toggleScript(id) {
    set("script_ids", draft.script_ids.includes(id)
      ? draft.script_ids.filter((x) => x !== id)
      : [...draft.script_ids, id].slice(0, 6));
  }

  async function save(event) {
    event.preventDefault();
    try {
      await patch.mutateAsync({ listId: list.id, body: {
        name: draft.name, external_list_id: draft.external_list_id || null,
        cadence: draft.cadence || null, kind: draft.kind, group_key: draft.group_key || null,
        block_key: draft.block_key || null, description: draft.description || null,
        script_ids: draft.script_ids, active: draft.active,
      } });
      onSaved(`${String(index + 1).padStart(2, "0")} ${draft.name}: ${savedText(Boolean(list.published_at))}`);
    } catch (err) {
      onError(errText(err));
    }
  }

  async function drop() {
    if (!window.confirm(`Remove “${list.name}” from Win the Day? Turning it off keeps it for later instead.`)) return;
    try {
      await remove.mutateAsync(list.id);
      onSaved(`Removed ${list.name}.`);
    } catch (err) {
      onError(errText(err));
    }
  }

  return (
    <form className={`wtdc-list ${draft.active ? "" : "inactive"}`} onSubmit={save}>
      <div className="wtdc-list-head">
        <strong>{String(index + 1).padStart(2, "0")}</strong>
        <div className="tile-actions">
          <button type="button" disabled={busy || index === 0} onClick={() => onMove(index, index - 1)}>{COPY.moveUp}</button>
          <button type="button" disabled={busy || index === total - 1} onClick={() => onMove(index, index + 1)}>{COPY.moveDown}</button>
        </div>
        <label className="tile-active">
          <input type="checkbox" checked={draft.active} onChange={(e) => set("active", e.target.checked)} />
          <span>In the run</span>
        </label>
      </div>
      <div className="wtdc-grid-3">
        <TextField label="Name" value={draft.name} max={120} onChange={(v) => set("name", v)} />
        <Field label="Follow Up Boss smart list">
          {smartLists.length ? (
            <select value={draft.external_list_id} onChange={(e) => set("external_list_id", e.target.value)}>
              <option value="">No list</option>
              {!smartLists.some((l) => String(l.id) === draft.external_list_id) && draft.external_list_id
                ? <option value={draft.external_list_id}>{`#${draft.external_list_id} (not in the account)`}</option> : null}
              {smartLists.map((l) => <option key={l.id} value={String(l.id)}>{`${l.name} (#${l.id})`}</option>)}
            </select>
          ) : (
            <input value={draft.external_list_id} placeholder="The number at the end of its address"
                   onChange={(e) => set("external_list_id", e.target.value.replace(/[^0-9]/g, ""))} />
          )}
        </Field>
        <TextField label="Cadence" value={draft.cadence} max={32} placeholder="Daily" onChange={(v) => set("cadence", v)} />
        <Field label="Kind">
          <select value={draft.kind} onChange={(e) => set("kind", e.target.value)}>
            {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </Field>
        <Field label="Under the heading">
          <select value={draft.group_key} onChange={(e) => set("group_key", e.target.value)}>
            <option value="">No heading</option>
            {groups.map((g) => <option key={g.key} value={g.key}>{g.label || g.key}</option>)}
          </select>
        </Field>
        <Field label="Worked in the block">
          <select value={draft.block_key} onChange={(e) => set("block_key", e.target.value)}>
            <option value="">No block</option>
            {blocks.map((b) => <option key={b.key} value={b.key}>{b.title || b.key}</option>)}
          </select>
        </Field>
      </div>
      <TextField label="What it is and how to work it" value={draft.description} max={600} multiline rows={2}
                 onChange={(v) => set("description", v)} />
      <fieldset className="wtdc-scripts-pick">
        <legend>Scripts (up to six, in the order picked)</legend>
        {scripts.length ? scripts.map((sc) => (
          <label key={sc.id}>
            <input type="checkbox" checked={draft.script_ids.includes(sc.id)} onChange={() => toggleScript(sc.id)} />
            <span>{sc.chip || sc.name}</span>
            {draft.script_ids.includes(sc.id) ? <em>{draft.script_ids.indexOf(sc.id) + 1}</em> : null}
          </label>
        )) : <p className="followup-hint">Add scripts on the Scripts section first.</p>}
      </fieldset>
      <div className="followup-actions">
        <Button type="submit" tone="primary" busy={patch.isPending}>Save list</Button>
        <Button type="button" onClick={drop} busy={remove.isPending}>Remove</Button>
        {href ? <a className="wtdc-link" href={href} target="_blank" rel="noreferrer">Open in Follow Up Boss</a>
              : <span className="followup-hint">{base ? "No smart list picked" : "Follow Up Boss is not connected"}</span>}
      </div>
    </form>
  );
}

function AddList({ smartLists }) {
  const create = useCreateWtdList();
  const [form, setForm] = useState({ name: "", external_list_id: "" });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  function pick(id) {
    const chosen = smartLists.find((l) => String(l.id) === id);
    setForm((f) => ({ ...f, external_list_id: id, name: f.name || (chosen ? chosen.name : "") }));
  }
  async function submit(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await create.mutateAsync({ name: form.name.trim(), external_list_id: form.external_list_id || null });
      setForm({ name: "", external_list_id: "" });
      setMessage("List added at the end of the run. Set its heading, block and scripts above.");
    } catch (err) {
      setError(errText(err));
    }
  }
  return (
    <Panel title="Add a call list">
      <form className="wtd-add" onSubmit={submit}>
        {smartLists.length ? (
          <Field label="Follow Up Boss smart list">
            <select value={form.external_list_id} onChange={(e) => pick(e.target.value)}>
              <option value="">Pick a smart list</option>
              {smartLists.map((l) => <option key={l.id} value={String(l.id)}>{l.name}</option>)}
            </select>
          </Field>
        ) : (
          <Field label="Smart list id">
            <input value={form.external_list_id} placeholder="The number at the end of the smart list's address"
                   onChange={(e) => setForm((f) => ({ ...f, external_list_id: e.target.value.replace(/[^0-9]/g, "") }))} />
          </Field>
        )}
        <Field label="Name">
          <input value={form.name} required onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </Field>
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={create.isPending}>Add list</Button>
          {message ? <span className="roster-form-message">{message}</span> : null}
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

/* WHICH IDS ARE NOT SMART LISTS IN THE ACCOUNT. Asked of Follow Up Boss on the server, with the
   stored key, only when somebody presses the button. */
function AccountCheck() {
  const check = useCheckWtdLists();
  const result = check.data;
  return (
    <div className="wtdc-check">
      <Button type="button" onClick={() => check.mutate()} busy={check.isPending}>Check the ids against Follow Up Boss</Button>
      {check.error ? <p className="console-form-error">{errText(check.error)}</p> : null}
      {result ? (
        result.missing.length ? (
          <div className="wtdc-check-result">
            <p className="console-form-error">{`${result.missing.length} of ${result.checked} ${result.checked === 1 ? "id is" : "ids are"} not a smart list in the account:`}</p>
            <ul>
              {result.missing.map((m) => <li key={`${m.kind}-${m.id}`}>{`${m.kind === "pond" ? "Pond" : "List"} “${m.name}” — #${m.id}`}</li>)}
            </ul>
          </div>
        ) : <p className="roster-form-message">{`All ${result.checked} ids are smart lists in the account.`}</p>
      ) : null}
    </div>
  );
}

function ListsSection({ playbook, scripts }) {
  const listsQuery = useWtdLists(true);
  const smart = useFubSmartLists();
  const order = useOrderWtdLists();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const lists = listsQuery.data?.items || [];
  const smartLists = smart.data?.items || [];

  async function move(from, to) {
    if (to < 0 || to >= lists.length) return;
    const ids = lists.map((l) => l.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    setMessage("");
    setError("");
    try {
      await order.mutateAsync({ ids });
      setMessage("Order saved as a draft.");
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <>
      <ListsText playbook={playbook} />
      <Panel title="Call lists, in the order agents run them" action={<AccountCheck />}>
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        {listsQuery.isPending ? <LoadingState /> : null}
        {listsQuery.error ? <ErrorState title={COPY.wtdError} onRetry={() => listsQuery.refetch()} /> : null}
        {!listsQuery.isPending && !lists.length ? <EmptyState title={COPY.wtdEmpty} /> : null}
        <div className="wtdc-lists">
          {lists.map((list, index) => (
            <ListCard key={list.id} list={list} index={index} total={lists.length} playbook={playbook}
                      scripts={scripts} smartLists={smartLists} busy={order.isPending} onMove={move}
                      onSaved={(m) => { setError(""); setMessage(m); }}
                      onError={(m) => { setMessage(""); setError(m); }} />
          ))}
        </div>
      </Panel>
      <AddList smartLists={smartLists} />
    </>
  );
}

// ── The Call ──────────────────────────────────────────────────────────────────────────────

function CallSection({ playbook }) {
  return (
    <SectionForm title="The Call" names={["call"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="How every list is worked: numbered steps, the compliance note, and the habits to keep.">
      {(d, set) => (
        <>
          <TextField label="Heading" value={d.call.title} max={120} placeholder="How to Work a List."
                     onChange={(v) => set("call", { title: v })} />
          <TextField label="Intro" value={d.call.intro} max={600} multiline rows={2}
                     onChange={(v) => set("call", { intro: v })} />
          <h3 className="wtdc-subhead">Steps</h3>
          <RepeatList items={d.call.steps} max={10} addLabel="Add a step"
                      make={() => ({ title: "", text: "" })}
                      onChange={(steps) => set("call", { steps })}
                      render={(t, setT) => TitleText(t, setT, "Step")} />
          <h3 className="wtdc-subhead">Compliance</h3>
          <TextField label="Eyebrow" value={d.call.compliance.eyebrow} max={120} placeholder="Compliance, on every call"
                     onChange={(v) => set("call", { compliance: { ...d.call.compliance, eyebrow: v } })} />
          <TextField label="Text" value={d.call.compliance.text} max={1500} multiline
                     onChange={(v) => set("call", { compliance: { ...d.call.compliance, text: v } })} />
          <h3 className="wtdc-subhead">Habits</h3>
          <TextField label="Heading" value={d.call.habits_title} max={80} placeholder="Keep These Habits Specifically"
                     onChange={(v) => set("call", { habits_title: v })} />
          <RepeatList items={d.call.habits} max={12} addLabel="Add a habit"
                      make={() => ({ title: "", text: "" })}
                      onChange={(habits) => set("call", { habits })}
                      render={(t, setT) => TitleText(t, setT, "Habit", "Why")} />
        </>
      )}
    </SectionForm>
  );
}

// ── Scripts ───────────────────────────────────────────────────────────────────────────────

function ScriptsText({ playbook }) {
  return (
    <SectionForm title="The scripts page" names={["scripts"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="The groups scripts are shown in, the link to the full library, and what to do when none fit. Save a new group before picking it on a script below.">
      {(d, set) => (
        <>
          <TextField label="Heading" value={d.scripts.title} max={120}
                     onChange={(v) => set("scripts", { title: v })} />
          <TextField label="Intro" value={d.scripts.intro} max={600} multiline rows={2}
                     onChange={(v) => set("scripts", { intro: v })} />
          <div className="wtdc-grid-2">
            <TextField label="Library link label" value={d.scripts.library.label} max={80} placeholder="Open the Full Script Library"
                       onChange={(v) => set("scripts", { library: { ...d.scripts.library, label: v } })} />
            <TextField label="Library link" value={d.scripts.library.url} max={500} placeholder="https://"
                       onChange={(v) => set("scripts", { library: { ...d.scripts.library, url: v || null } })} />
          </div>
          <h3 className="wtdc-subhead">Groups</h3>
          <RepeatList items={d.scripts.groups} max={8} addLabel="Add a group"
                      make={() => ({ key: newKey("sg"), label: "", sub: "" })}
                      onChange={(groups) => set("scripts", { groups })}
                      render={(g, setG) => (
                        <div className="wtdc-grid-2">
                          <TextField label="Group" value={g.label} max={120} placeholder="Leads and web enquiries" onChange={(v) => setG({ label: v })} />
                          <TextField label="Beside it" value={g.sub} max={80} placeholder="Lists 1, 2, 3, 10, 13" onChange={(v) => setG({ sub: v })} />
                        </div>
                      )} />
          <h3 className="wtdc-subhead">When none of these fit</h3>
          <TextField label="Eyebrow" value={d.scripts.fallback.eyebrow} max={120} placeholder="If none of these fit the call"
                     onChange={(v) => set("scripts", { fallback: { ...d.scripts.fallback, eyebrow: v } })} />
          <TextField label="Text" value={d.scripts.fallback.text} max={1500} multiline
                     onChange={(v) => set("scripts", { fallback: { ...d.scripts.fallback, text: v } })} />
        </>
      )}
    </SectionForm>
  );
}

function scriptDraft(sc) {
  return { name: sc.name || "", chip: sc.chip || "", url: sc.url || "", description: sc.description || "",
           group_key: sc.group_key || "", active: Boolean(sc.active) };
}

function ScriptRow({ script, index, total, groups, onMove, onSaved, onError }) {
  const patch = usePatchWtdScript();
  const remove = useDeleteWtdScript();
  const [draft, setDraft] = useState(() => scriptDraft(script));
  useEffect(() => { setDraft(scriptDraft(script)); }, [script]);
  const set = (field, value) => setDraft((d) => ({ ...d, [field]: value }));
  async function save(event) {
    event.preventDefault();
    try {
      await patch.mutateAsync({ scriptId: script.id, body: {
        name: draft.name, chip: draft.chip || null, url: draft.url || null,
        description: draft.description || null, group_key: draft.group_key || null, active: draft.active,
      } });
      onSaved(`${draft.name}: ${savedText(Boolean(script.published_at))}`);
    } catch (err) {
      onError(errText(err));
    }
  }
  async function drop() {
    if (!window.confirm(`Remove “${script.name}”? It comes off every list that uses it.`)) return;
    try {
      await remove.mutateAsync(script.id);
      onSaved(`Removed ${script.name}.`);
    } catch (err) {
      onError(errText(err));
    }
  }
  return (
    <form className={`wtdc-list ${draft.active ? "" : "inactive"}`} onSubmit={save}>
      <div className="wtdc-list-head">
        <strong>{index + 1}</strong>
        <div className="tile-actions">
          <button type="button" disabled={index === 0} onClick={() => onMove(index, index - 1)}>{COPY.moveUp}</button>
          <button type="button" disabled={index === total - 1} onClick={() => onMove(index, index + 1)}>{COPY.moveDown}</button>
        </div>
        <label className="tile-active">
          <input type="checkbox" checked={draft.active} onChange={(e) => set("active", e.target.checked)} />
          <span>Active</span>
        </label>
      </div>
      <div className="wtdc-grid-3">
        <TextField label="Name" value={draft.name} max={120} onChange={(v) => set("name", v)} />
        <TextField label="Chip label on lists" value={draft.chip} max={40} placeholder="Blank uses the name" onChange={(v) => set("chip", v)} />
        <Field label="Group on the Scripts tab">
          <select value={draft.group_key} onChange={(e) => set("group_key", e.target.value)}>
            <option value="">No group (a chip on lists only)</option>
            {groups.map((g) => <option key={g.key} value={g.key}>{g.label || g.key}</option>)}
          </select>
        </Field>
      </div>
      <TextField label="Link" value={draft.url} max={500} placeholder="https://" onChange={(v) => set("url", v)} />
      <TextField label="One line on when to use it" value={draft.description} max={400} multiline rows={2}
                 onChange={(v) => set("description", v)} />
      <div className="followup-actions">
        <Button type="submit" tone="primary" busy={patch.isPending}>Save script</Button>
        <Button type="button" onClick={drop} busy={remove.isPending}>Remove</Button>
      </div>
    </form>
  );
}

function ScriptsSection({ playbook, scriptsQuery }) {
  const create = useCreateWtdScript();
  const order = useOrderWtdScripts();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const scripts = scriptsQuery.data?.items || [];
  const groups = playbook.content.scripts.groups;

  async function add(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await create.mutateAsync({ name: name.trim() });
      setName("");
      setMessage("Script added. Give it a link, a line and a group.");
    } catch (err) {
      setError(errText(err));
    }
  }
  async function move(from, to) {
    if (to < 0 || to >= scripts.length) return;
    const ids = scripts.map((sc) => sc.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    try {
      await order.mutateAsync({ ids });
    } catch (err) {
      setError(errText(err));
    }
  }
  return (
    <>
      <ScriptsText playbook={playbook} />
      <Panel title="The script library">
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        {scriptsQuery.isPending ? <LoadingState /> : null}
        {!scriptsQuery.isPending && !scripts.length ? <EmptyState title="No scripts yet." /> : null}
        <div className="wtdc-lists">
          {scripts.map((sc, index) => (
            <ScriptRow key={sc.id} script={sc} index={index} total={scripts.length} groups={groups}
                       onMove={move} onSaved={(m) => { setError(""); setMessage(m); }}
                       onError={(m) => { setMessage(""); setError(m); }} />
          ))}
        </div>
        <form className="wtdc-inline-add" onSubmit={add}>
          <Field label="New script">
            <input value={name} required maxLength={120} placeholder="LPMAMA" onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button type="submit" busy={create.isPending}>Add script</Button>
        </form>
      </Panel>
    </>
  );
}

// ── Numbers & targets ─────────────────────────────────────────────────────────────────────

function NumbersSection({ playbook }) {
  return (
    <>
      <SectionForm title="The scoreboard and the on-ramp" names={["numbers"]} content={playbook.content} live={Boolean(playbook.published_at)}
                   intro="Rows that count on Today's sheet get a − and + there, against their daily goal. The on-ramp gives a new agent lower goals for their first working days; set a person's start date below to put them on it.">
        {(d, set) => {
          const tallies = d.numbers.rows.filter((r) => r.tally).map((r) => r.tally);
          return (
            <>
              <TextField label="Heading" value={d.numbers.title} max={120}
                         onChange={(v) => set("numbers", { title: v })} />
              <TextField label="Intro" value={d.numbers.intro} max={600} multiline rows={2}
                         onChange={(v) => set("numbers", { intro: v })} />
              <h3 className="wtdc-subhead">Scoreboard rows</h3>
              <RepeatList items={d.numbers.rows} max={12} addLabel="Add a row"
                          make={() => ({ label: "", daily: "", weekly: "", highlight: false, tally: null })}
                          onChange={(rows) => set("numbers", { rows })}
                          render={(r, setR) => (
                            <>
                              <div className="wtdc-grid-3">
                                <TextField label="Metric" value={r.label} max={80} onChange={(v) => setR({ label: v })} />
                                <TextField label="Daily target (as shown)" value={r.daily} max={40} placeholder="25 / day" onChange={(v) => setR({ daily: v })} />
                                <TextField label="Weekly target (as shown)" value={r.weekly} max={40} placeholder="125 / week" onChange={(v) => setR({ weekly: v })} />
                              </div>
                              <div className="wtdc-toggles">
                                <label className="followup-switch">
                                  <input type="checkbox" checked={Boolean(r.highlight)} onChange={(e) => setR({ highlight: e.target.checked })} />
                                  <span>The number that matters most</span>
                                </label>
                                <label className="followup-switch">
                                  <input type="checkbox" checked={Boolean(r.tally)}
                                         onChange={(e) => setR({ tally: e.target.checked ? { key: newKey("t"), short: "", goal: null } : null })} />
                                  <span>Counted on Today&rsquo;s sheet</span>
                                </label>
                              </div>
                              {r.tally ? (
                                <div className="wtdc-grid-2">
                                  <NumberField label="Daily goal" value={r.tally.goal} max={10000} placeholder="No goal"
                                               onChange={(v) => setR({ tally: { ...r.tally, goal: v } })} />
                                  <TextField label="Short name (the on-ramp cards)" value={r.tally.short} max={24} placeholder="dials"
                                             onChange={(v) => setR({ tally: { ...r.tally, short: v } })} />
                                </div>
                              ) : null}
                            </>
                          )} />
              <TextField label="Footnote" value={d.numbers.footnote} max={600} multiline rows={2}
                         onChange={(v) => set("numbers", { footnote: v })} />
              <h3 className="wtdc-subhead">The new agent on-ramp</h3>
              <TextField label="Heading" value={d.numbers.onramp.title} max={80} placeholder="The New Agent On-ramp"
                         onChange={(v) => set("numbers", { onramp: { ...d.numbers.onramp, title: v } })} />
              <TextField label="Intro" value={d.numbers.onramp.intro} max={600} multiline rows={2}
                         onChange={(v) => set("numbers", { onramp: { ...d.numbers.onramp, intro: v } })} />
              <RepeatList items={d.numbers.onramp.phases} max={6} addLabel="Add a phase" emptyLabel="No on-ramp: everybody gets the team's goals."
                          make={() => ({ label: "", through_day: null, goals: {}, focus: "" })}
                          onChange={(phases) => set("numbers", { onramp: { ...d.numbers.onramp, phases } })}
                          render={(ph, setPh) => (
                            <>
                              <div className="wtdc-grid-2">
                                <TextField label="Phase" value={ph.label} max={40} placeholder="Week One" onChange={(v) => setPh({ label: v })} />
                                <NumberField label="Through working day (blank on the last: onward)" value={ph.through_day} min={1} max={365}
                                             onChange={(v) => setPh({ through_day: v })} />
                              </div>
                              <div className="wtdc-grid-3">
                                {tallies.filter((t) => t.goal !== null && t.goal !== undefined || ph.goals?.[t.key] !== undefined).map((t) => (
                                  <NumberField key={t.key} label={`${t.short || t.key} goal`} value={ph.goals?.[t.key]} max={10000}
                                               placeholder={t.goal ?? ""}
                                               onChange={(v) => {
                                                 const goals = { ...(ph.goals || {}) };
                                                 if (v === null) delete goals[t.key]; else goals[t.key] = v;
                                                 setPh({ goals });
                                               }} />
                                ))}
                              </div>
                              <TextField label="Focus" value={ph.focus} max={300} multiline rows={2} onChange={(v) => setPh({ focus: v })} />
                            </>
                          )} />
            </>
          );
        }}
      </SectionForm>
      <PeopleTargets />
    </>
  );
}

function PersonRow({ person, tallies }) {
  const save = usePatchWtdPerson();
  const [started, setStarted] = useState(person.started_on || "");
  const [goals, setGoals] = useState(person.wtd_goals || {});
  const [note, setNote] = useState("");
  useEffect(() => {
    setStarted(person.started_on || "");
    setGoals(person.wtd_goals || {});
  }, [person]);
  async function submit(event) {
    event.preventDefault();
    setNote("");
    try {
      const clean = Object.fromEntries(Object.entries(goals).filter(([, v]) => v !== null && v !== ""));
      await save.mutateAsync({ memberId: person.id, body: { started_on: started || null, wtd_goals: clean } });
      setNote("Saved");
    } catch (err) {
      setNote(errText(err));
    }
  }
  return (
    <tr>
      <th scope="row">{person.name}{person.status !== "Active" ? <small>{` · ${person.status}`}</small> : null}</th>
      <td><input type="date" value={started} onChange={(e) => setStarted(e.target.value)} aria-label={`${person.name}'s start date`} /></td>
      {tallies.map((t) => (
        <td key={t.key}>
          <input type="number" min={0} max={10000} value={goals[t.key] ?? ""} placeholder={person.goals_today?.[t.key] ?? ""}
                 aria-label={`${person.name}'s ${t.label} goal`}
                 onChange={(e) => setGoals((g) => ({ ...g, [t.key]: e.target.value === "" ? null : Number(e.target.value) }))} />
        </td>
      ))}
      <td className="wtdc-today">{person.onramp_phase || "Team targets"}</td>
      <td>
        <form onSubmit={submit} className="wtdc-row-save">
          <Button type="submit" busy={save.isPending}>Save</Button>
          {note ? <small>{note}</small> : null}
        </form>
      </td>
    </tr>
  );
}

/* EACH PERSON'S OWN NUMBERS. A start date puts somebody on the on-ramp; a number here beats the
   on-ramp and the team's. Saved on their roster row, immediately -- people are not content. */
function PeopleTargets() {
  const people = useWtdPeople();
  if (people.isPending) return <Panel title="Each person&rsquo;s targets"><LoadingState /></Panel>;
  if (people.error) return <Panel title="Each person's targets"><ErrorState onRetry={() => people.refetch()} /></Panel>;
  const { tallies, items } = people.data;
  return (
    <Panel title="Each person&rsquo;s targets">
      <p className="followup-intro">
        A start date puts somebody on the on-ramp, counted in working days. A number here is their
        own and beats both the on-ramp and the team&rsquo;s. Grey numbers are what applies today.
      </p>
      {!tallies.length ? <p className="followup-hint">Count a scoreboard row on Today&rsquo;s sheet first.</p> : null}
      {items.length ? (
        <div className="wtdc-table-wrap">
          <table className="wtdc-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>On-ramp start</th>
                {tallies.map((t) => <th key={t.key}>{t.label}</th>)}
                <th>Today</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((p) => <PersonRow key={p.id} person={p} tallies={tallies} />)}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title="Nobody on the roster yet." />}
    </Panel>
  );
}

// ── Tools ─────────────────────────────────────────────────────────────────────────────────

function ToolsSection({ playbook }) {
  return (
    <SectionForm title="Tools" names={["tools"]} content={playbook.content} live={Boolean(playbook.published_at)}
                 intro="The tools that make a block of the day sharper, each with the block it belongs to.">
      {(d, set) => (
        <>
          <TextField label="Heading" value={d.tools.title} max={120} placeholder="The Tools that Stay in Your Day."
                     onChange={(v) => set("tools", { title: v })} />
          <TextField label="Intro" value={d.tools.intro} max={600} multiline rows={2}
                     onChange={(v) => set("tools", { intro: v })} />
          <RepeatList items={d.tools.items} max={12} addLabel="Add a tool"
                      make={() => ({ block: "", name: "", url: null, tagline: "", text: "" })}
                      onChange={(items) => set("tools", { items })}
                      render={(t, setT) => (
                        <>
                          <div className="wtdc-grid-3">
                            <TextField label="Name" value={t.name} max={80} onChange={(v) => setT({ name: v })} />
                            <TextField label="Block" value={t.block} max={60} placeholder="Block 1 · Power Up" onChange={(v) => setT({ block: v })} />
                            <TextField label="Link" value={t.url} max={500} placeholder="https://" onChange={(v) => setT({ url: v || null })} />
                          </div>
                          <TextField label="Tagline" value={t.tagline} max={160} onChange={(v) => setT({ tagline: v })} />
                          <TextField label="Text" value={t.text} max={600} multiline rows={2} onChange={(v) => setT({ text: v })} />
                        </>
                      )} />
        </>
      )}
    </SectionForm>
  );
}

// ── Needs You Today ───────────────────────────────────────────────────────────────────────

/* WHAT NEEDS YOU TODAY LISTS, set by the workspace rather than compiled in. The rules are Follow Up
 * Boss's own idea of a follow-up (services/follow_ups); these are the three numbers and the one
 * switch a team would reasonably disagree about. Going cold stays off until somebody picks the
 * stages it applies to, because they are this account's own names and a default would be a
 * guess. */
function numberOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function FollowUpSettings() {
  const query = useFollowUpSettings();
  const save = usePatchFollowUpSettings();
  const [form, setForm] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (query.data) setForm(query.data.settings);
  }, [query.data]);

  if (query.isPending) return <Panel title="Needs You Today"><LoadingState /></Panel>;
  if (query.error) return <Panel title="Needs You Today"><ErrorState onRetry={() => query.refetch()} /></Panel>;
  if (!form) return null;

  const data = query.data;
  const bounds = data.bounds || {};
  const stages = data.stages || [];
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const toggleStage = (stage) => set("cold_stages", form.cold_stages.includes(stage)
    ? form.cold_stages.filter((s) => s !== stage)
    : [...form.cold_stages, stage]);

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await save.mutateAsync({
        new_lead_days: numberOr(form.new_lead_days, 7),
        overdue_max_days: numberOr(form.overdue_max_days, 30),
        cold_enabled: Boolean(form.cold_enabled),
        cold_days: numberOr(form.cold_days, 14),
        cold_stages: form.cold_stages,
      });
      setMessage("Saved. The portal uses these on the next page load.");
    } catch (err) {
      setError(errText(err));
    }
  }

  const conn = data.connection || {};
  return (
    <Panel title="Needs You Today">
      <form className="followup-settings" onSubmit={submit}>
        <p className="followup-intro">
          What each agent sees at the top of the portal, from Follow Up Boss: new leads nobody has
          contacted, and tasks due today or overdue. Leaders see the whole team.
          {conn.state === "not_connected" ? " Follow Up Boss is not connected yet — connect it on the Axcion dashboard under Settings, Integrations." : ""}
        </p>
        <div className="followup-fields">
          <Field label="New leads from the last (days)">
            <input type="number" min={bounds.new_lead_days?.min} max={bounds.new_lead_days?.max}
                   value={form.new_lead_days} onChange={(e) => set("new_lead_days", e.target.value)} />
          </Field>
          <Field label="Overdue tasks from the last (days; 0 lists every one)">
            <input type="number" min={bounds.overdue_max_days?.min} max={bounds.overdue_max_days?.max}
                   value={form.overdue_max_days} onChange={(e) => set("overdue_max_days", e.target.value)} />
          </Field>
        </div>
        {data.older_overdue ? (
          <p className="followup-hint">
            {`${data.older_overdue.toLocaleString()} older overdue tasks across the team are outside this window and not listed.`}
          </p>
        ) : null}
        <label className="followup-switch">
          <input type="checkbox" checked={Boolean(form.cold_enabled)}
                 onChange={(e) => set("cold_enabled", e.target.checked)} />
          <span>Also list leads going cold</span>
        </label>
        {form.cold_enabled ? (
          <div className="followup-cold">
            <Field label="Quiet for at least (days)">
              <input type="number" min={bounds.cold_days?.min} max={bounds.cold_days?.max}
                     value={form.cold_days} onChange={(e) => set("cold_days", e.target.value)} />
            </Field>
            <fieldset className="followup-stages">
              <legend>In these stages</legend>
              {stages.length ? stages.map((s) => (
                <label key={s.stage}>
                  <input type="checkbox" checked={form.cold_stages.includes(s.stage)}
                         onChange={() => toggleStage(s.stage)} />
                  <span>{s.stage}</span>
                  <em>{s.people.toLocaleString()}</em>
                </label>
              )) : <p className="followup-hint">Stages appear here after Follow Up Boss has synced.</p>}
            </fieldset>
            {!form.cold_stages.length ? <p className="followup-hint">Pick at least one stage; until then nothing is listed as going cold.</p> : null}
          </div>
        ) : null}
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={save.isPending}>Save</Button>
          {message ? <span className="roster-form-message">{message}</span> : null}
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

// ── moving a playbook in and out ──────────────────────────────────────────────────────────

/* EXPORT AND IMPORT. A playbook leaves as one JSON file -- its document, lists and scripts -- and
   arrives the same way, as a draft. Import refuses to overwrite until somebody confirms it. */
function MoveBar({ playbook }) {
  const importer = useImportWtdPlaybook();
  const fileRef = useRef(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function download() {
    setMessage("");
    setError("");
    try {
      const bundle = await exportWtdPlaybook();
      const url = URL.createObjectURL(new Blob([`${JSON.stringify(bundle, null, 2)}\n`], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "win-the-day-playbook.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(errText(err));
    }
  }

  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setMessage("");
    setError("");
    let bundle;
    try {
      bundle = JSON.parse(await file.text());
    } catch {
      setError("That file is not a playbook: it is not JSON.");
      return;
    }
    try {
      const done = await importer.mutateAsync({ bundle });
      setMessage(`Imported ${done.lists} lists and ${done.scripts} scripts as a draft. Read it through, then publish.`);
    } catch (err) {
      if (err.status !== 409) {
        setError(errText(err));
        return;
      }
      if (!window.confirm("This workspace already has a Win the Day playbook. Replace it with the file? The current playbook, lists and scripts are removed now; the file's appear when you publish.")) return;
      try {
        const done = await importer.mutateAsync({ bundle, replace: true });
        setMessage(`Replaced with ${done.lists} lists and ${done.scripts} scripts, as a draft. Read it through, then publish.`);
      } catch (err2) {
        setError(errText(err2));
      }
    }
  }

  const status = playbook.exists
    ? (playbook.published_at ? `Published ${new Date(playbook.published_at).toLocaleDateString()}${playbook.draft_dirty ? " · unpublished changes" : ""}` : "Draft · not yet published")
    : "No playbook yet";
  return (
    <section className="wtdc-bar">
      <div>
        <span>Playbook</span>
        <strong>{status}</strong>
      </div>
      <div className="followup-actions">
        <Button type="button" onClick={download}>Export</Button>
        <Button type="button" onClick={() => fileRef.current?.click()} busy={importer.isPending}>Import a file</Button>
        <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={upload} />
      </div>
      {message ? <p className="roster-form-message">{message}</p> : null}
      {error ? <p className="console-form-error">{error}</p> : null}
    </section>
  );
}

// ── the page ──────────────────────────────────────────────────────────────────────────────

export default function WinTheDay() {
  const [params, setParams] = useSearchParams();
  const section = SECTIONS.some((s) => s.key === params.get("section")) ? params.get("section") : "page";
  const playbookQuery = useWtdPlaybook();
  const scriptsQuery = useWtdScripts();
  const scripts = useMemo(() => scriptsQuery.data?.items || [], [scriptsQuery.data]);

  if (playbookQuery.isPending) return <LoadingState />;
  if (playbookQuery.error) return <ErrorState title={COPY.wtdError} onRetry={() => playbookQuery.refetch()} />;
  const playbook = playbookQuery.data;

  return (
    <div className="wtd-grid">
      <MoveBar playbook={playbook} />
      <nav className="wtdc-sections" aria-label="Win the Day sections">
        {SECTIONS.map((s) => (
          <button key={s.key} type="button" className={s.key === section ? "on" : ""}
                  aria-current={s.key === section ? "page" : undefined}
                  onClick={() => setParams({ section: s.key })}>
            {s.label}
          </button>
        ))}
      </nav>
      {section === "page" ? <PageSection playbook={playbook} /> : null}
      {section === "run" ? <RunSection playbook={playbook} /> : null}
      {section === "lists" ? <ListsSection playbook={playbook} scripts={scripts} /> : null}
      {section === "call" ? <CallSection playbook={playbook} /> : null}
      {section === "scripts" ? <ScriptsSection playbook={playbook} scriptsQuery={scriptsQuery} /> : null}
      {section === "numbers" ? <NumbersSection playbook={playbook} /> : null}
      {section === "tools" ? <ToolsSection playbook={playbook} /> : null}
      {section === "followups" ? <FollowUpSettings /> : null}
    </div>
  );
}
