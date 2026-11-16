import { useEffect, useState } from "react";
import { post } from "./useMeeting.js";
import { useCopy } from "./format.js";
import * as I from "./icons.jsx";

const weekLabel = (monday) => {
  const d = new Date(monday + "T00:00");
  const end = new Date(d); end.setDate(d.getDate() + 6);
  const f = (x) => x.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${f(d)} – ${f(end)}`;
};

// End-of-week reviews (vault/wiki/weekly/). Written by itself on Friday afternoon, or on demand here.
export default function Weekly({ event, onOpenMeeting }) {
  const [weeks, setWeeks] = useState(null);
  const [week, setWeek] = useState(null);
  const [doc, setDoc] = useState(null);
  const [busy, setBusy] = useState(null);     // week being written
  const [error, setError] = useState("");
  const [copied, copy] = useCopy();

  const loadList = () => fetch("/weekly").then((r) => r.json()).then((d) => {
    setWeeks(d.weeks); setBusy(d.busy);
    setWeek((w) => w || d.weeks[0]?.week || null);
  }).catch(() => setWeeks([]));
  const loadWeek = (w) => fetch(`/weekly/${w}`).then((r) => r.json()).then(setDoc);

  useEffect(() => { loadList(); }, []);
  useEffect(() => { setDoc(null); setError(""); if (week) loadWeek(week); }, [week]);
  // Written in the background (the Friday timer, or another tab).
  useEffect(() => {
    if (!event) return;
    setBusy(event.state === "writing" ? event.week : null);
    if (event.state === "failed" && event.week === week) setError(event.error || "Couldn't write the review.");
    if (event.state === "ready") { loadList(); if (event.week === week) loadWeek(week); }
  }, [event]);

  const write = async () => {
    setBusy(week); setError("");
    try {
      const d = await (await post("/weekly", { week })).json();
      if (d.error) setError(d.error); else setDoc(d);
      loadList();
    } catch { setError("Can't reach the server. Is copilot.py running?"); }
    setBusy(null);
  };

  const sel = weeks?.find((w) => w.week === week);
  const writing = busy === week;

  return (
    <div className="page">
      <div className="page-inner">
        <div className="meeting-head">
          <div className="avatar"><I.Calendar size={18} /></div>
          <div>
            <h2>Weekly review</h2>
            <div className="meta">What happened this week, what you have to do, and what to know. Written every Friday afternoon.</div>
          </div>
        </div>

        {weeks === null && <p className="muted">Loading…</p>}
        {weeks?.length === 0 && <p className="muted">No meetings yet. Reviews show up here once you've recorded some.</p>}
        {weeks?.length > 0 && (
          <div className="week-chips">
            {weeks.map((w) => (
              <button key={w.week} className={`chip ${w.week === week ? "on" : ""}`} onClick={() => setWeek(w.week)}>
                {weekLabel(w.week)} <span className="muted">· {w.meetings}</span>
                {!w.written && <i className="todo-dot" title="No review yet" />}
              </button>
            ))}
          </div>
        )}

        {sel && (
          <div className="card overview">
            <div className="card-row">
              <h3>{doc?.title || `Week of ${weekLabel(sel.week)}`}
                {doc?.generated && <span className="muted"> · written {doc.generated.slice(5)}</span>}</h3>
              <div className="row-actions">
                {doc?.written && (
                  <button className={`pill sm ghost ${copied ? "done" : ""}`} onClick={() => copy(1, doc.markdown)}>
                    {copied ? <><I.Check size={13} /> Copied</> : <><I.Copy size={13} /> Copy</>}
                  </button>
                )}
                <button className="pill sm primary" disabled={!!busy} onClick={write}>
                  {writing ? "Writing… (about a minute)" : doc?.written ? <><I.Refresh size={13} /> Rewrite</> : <><I.Sparkle size={13} /> Write review</>}
                </button>
              </div>
            </div>
            {error && <p className="status-err">{error}</p>}
            {!doc ? <p className="muted">Loading…</p> : !doc.written ? (
              <p className="overview-text muted">
                {writing ? "Reading this week's meetings…" : `No review for this week yet. It's written automatically on Friday afternoon, or click Write review now.`}
              </p>
            ) : (
              <>
                <p className="overview-text">{doc.summary}</p>
                {Object.entries(doc.sections).filter(([, x]) => x.length).map(([t, items]) => (
                  <div key={t} className="overview-sec"><h4>{t}</h4><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
                ))}
              </>
            )}
          </div>
        )}

        {doc?.meetings?.length > 0 && (
          <>
            <div className="section-head">Meetings this week</div>
            <div className="week-meetings">
              {doc.meetings.map((m) => (
                <button key={m.id} className="card week-meeting" onClick={() => onOpenMeeting(m.id)}>
                  <span className="muted">{m.label}</span> <b>{m.title || "Untitled meeting"}</b>
                  <span className="muted"> · {m.minutes} min</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
