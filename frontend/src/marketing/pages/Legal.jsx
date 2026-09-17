import React from "react";
import { CADET, CORE, NEUTRAL, TYPE } from "../../brand/acumyn.jsx";
import { DOCS, LEGAL_FACTS, LEGAL_IS_DRAFT } from "../legal.js";
import { body, h1Type, label, Section, displayType } from "../ui.jsx";

/* /privacy and /terms — Acumyn's own documents.
 *
 * These replaced Spring Command Center's privacy.html and eula.html, which every host used to
 * serve with Spring's name and unfilled blanks. They are what Google's OAuth
 * verification and a customer's security review will read — so the text lives in legal.js, is
 * written only from what the code does, and every fact that only the company can supply renders
 * as a visible blank until it is filled in, with a draft notice above it. A policy that looks
 * finished and names the wrong company is worse than one that plainly is not finished.
 */

function Fact({ name }) {
  const value = LEGAL_FACTS[name]?.value;
  if (value) return <>{value}</>;
  return (
    <mark style={{
      background: CADET[100], color: CADET[800], borderRadius: 4, padding: "0 4px",
      fontFamily: TYPE.text, fontWeight: 600,
    }}>[{LEGAL_FACTS[name]?.ask || name}]</mark>
  );
}

/* A paragraph is a list of pieces: strings, or {fact: "name"} for a company fact. */
function Text({ parts }) {
  return (
    <>
      {(Array.isArray(parts) ? parts : [parts]).map((p, i) => (
        typeof p === "string" ? <React.Fragment key={i}>{p}</React.Fragment> : <Fact key={i} name={p.fact} />
      ))}
    </>
  );
}

const para = { ...body, fontSize: 16, lineHeight: 1.7, maxWidth: "68ch" };

function Block({ block }) {
  if (block.list) {
    return (
      <ul style={{ ...para, paddingLeft: 22, display: "grid", gap: 8 }}>
        {block.list.map((item, i) => <li key={i}><Text parts={item} /></li>)}
      </ul>
    );
  }
  if (block.table) {
    return (
      <div style={{ overflowX: "auto", maxWidth: "100%" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 560, fontFamily: TYPE.text, fontSize: 14.5 }}>
          <thead>
            <tr>
              {block.table.head.map((h) => (
                <th key={h} scope="col" style={{
                  ...label, textAlign: "left", padding: "10px 12px 10px 0",
                  borderBottom: `1px solid ${NEUTRAL[200]}`,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.table.rows.map((row) => (
              <tr key={row[0]}>
                {row.map((cell, i) => (
                  <td key={i} style={{
                    padding: "12px 12px 12px 0", verticalAlign: "top", lineHeight: 1.55,
                    color: i === 0 ? CORE.ink : NEUTRAL[600], fontWeight: i === 0 ? 600 : 400,
                    borderBottom: `1px solid ${NEUTRAL[100]}`,
                  }}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return <p style={para}><Text parts={block} /></p>;
}

export default function Legal({ doc }) {
  const d = DOCS[doc];
  return (
    <Section style={{ paddingBottom: "clamp(56px, 8vw, 96px)" }}>
      <p style={{ ...label, color: CORE.cadet }}>Legal</p>
      <h1 style={{ ...displayType, fontSize: "clamp(36px, 5.4vw, 56px)", marginTop: 18 }}>{d.title}</h1>
      <p style={{ ...body, marginTop: 16 }}>
        Effective <Fact name="effectiveDate" />
      </p>

      {LEGAL_IS_DRAFT ? (
        /* Renders only while a company fact is missing, so an unfinished document cannot go live
           looking finished. Fill LEGAL_FACTS in legal.js and it disappears. */
        <p role="note" style={{
          ...body, fontSize: 14, color: CADET[700], background: CADET[50],
          border: `1px solid ${CADET[200]}`, borderRadius: 10, padding: "12px 16px",
          marginTop: 28, maxWidth: "68ch",
        }}>
          <strong style={{ fontWeight: 600 }}>Draft.</strong>{" "}
          This document is not yet in effect. The highlighted details are still to be confirmed.
        </p>
      ) : null}

      <p style={{ ...para, marginTop: 32 }}><Text parts={d.intro} /></p>

      <div style={{ display: "grid", gap: 40, marginTop: 44 }}>
        {d.sections.map((s) => (
          <section key={s.h} aria-labelledby={`legal-${s.id}`} style={{ display: "grid", gap: 14 }}>
            <h2 id={`legal-${s.id}`} style={{ ...h1Type, fontSize: "clamp(20px, 2.4vw, 24px)" }}>{s.h}</h2>
            {s.blocks.map((b, i) => <Block key={i} block={b} />)}
          </section>
        ))}
      </div>
    </Section>
  );
}
