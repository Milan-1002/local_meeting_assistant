import { useEffect, useState } from "react";
import Speakers from "./Speakers.jsx";
import Transcript, { TranscriptHead } from "./Transcript.jsx";
import { initial, when } from "./format.js";
import * as I from "./icons.jsx";

// Past meetings from the vault: a feed grouped by day, and a read-only page per meeting.
export default function Meetings({ name, query, openId, onOpen, voices, onName, onForget, kb, onRecompile }) {
  const [list, setList] = useState(null);
  const [open, setOpen] = useState(null);
  const stale = kb?.stale || 0;
  const busy = kb?.state === "compiling";

  // Reload when notes change: after a compile finishes, or when naming marks some for recompile.
  useEffect(() => {
    if (busy) return;
    fetch("/meetings").then((r) => r.json()).then(setList).catch(() => setList([]));
  }, [busy, stale]);
  useEffect(() => {
    setOpen(null);
    if (openId) fetch(`/meetings/${openId}`).then((r) => r.json()).then(setOpen);
  }, [openId]);

  if (openId) return <MeetingPage m={open} name={name} query={query} voices={voices} onName={onName} onForget={onForget} onBack={() => onOpen(null)} />;

  const q = query.toLowerCase();
  const shown = (list || []).filter((m) => !q || `${m.title} ${m.summary}`.toLowerCase().includes(q));
  const days = [];
  for (const m of shown) {
    const day = when(m.date).day;
    if (days[days.length - 1]?.day !== day) days.push({ day, items: [] });
    days[days.length - 1].items.push(m);
  }

  return (
    <div className="page">
      <div className="page-inner">
        {list === null && <p className="muted">Loading…</p>}
        {list?.length === 0 && <p className="muted">No past meetings yet. When you end a meeting, it shows up here.</p>}
        {list?.length > 0 && !shown.length && <p className="muted">No meetings match “{query}”.</p>}
        {(stale > 0 || busy) && (
          <div className="card recompile">
            <p>
              {busy ? "Updating meeting notes…"
                : <>{stale} meeting note{stale > 1 ? "s were" : " was"} written before you named a speaker in {stale > 1 ? "them" : "it"}.
                    Recompiling rewrites {stale > 1 ? "them" : "it"} with the names (one AI call each).</>}
            </p>
            {!busy && <button className="pill primary" onClick={onRecompile}>Recompile {stale}</button>}
          </div>
        )}
        {days.map((d) => (
          <section key={d.day}>
            <div className="day-head">{d.day}</div>
            {d.items.map((m) => (
              <div key={m.id} className="feed-item">
                <div className="avatar">{initial(name)}</div>
                <button className="card feed-card" onClick={() => onOpen(m.id)}>
                  <h3>{m.title || "Untitled meeting"}</h3>
                  <div className="meta">
                    {when(m.date).time} · {m.minutes} min · {name}
                    {!m.compiled && <span className="tag">Not in knowledge base yet</span>}
                    {m.stale && <span className="tag">Needs recompile</span>}
                  </div>
                  <p className="feed-summary">{m.summary}</p>
                  <span className="show-more">Open</span>
                </button>
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

function MeetingPage({ m, name, query, onBack, voices, onName, onForget }) {
  return (
    <div className="page">
      <div className="page-inner">
        <button className="back" onClick={onBack}><I.ChevronL size={16} /> All meetings</button>
        {!m ? <p className="muted">Loading…</p> : m.error ? <p className="muted">Meeting not found.</p> : (
          <>
            <div className="day-head">{when(m.date).day}</div>
            <div className="meeting-head">
              <div className="avatar">{initial(name)}</div>
              <div>
                <h2>{m.title || "Untitled meeting"}</h2>
                <div className="meta">{when(m.date).time} · {m.lines.length ? m.lines[m.lines.length - 1].clock : "0:00"} · {name}</div>
              </div>
            </div>
            <Speakers lines={m.lines} voices={voices} name={name} onName={onName} onForget={onForget} />
            {Object.keys(m.sections).length > 0 ? (
              <div className="card overview">
                {Object.entries(m.sections).filter(([, x]) => x.length).map(([t, items]) => (
                  <div key={t} className="overview-sec"><h4>{t}</h4><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
                ))}
              </div>
            ) : (
              <div className="card overview"><p className="overview-text muted">
                Not in the knowledge base yet. Run <code>python kb.py compile</code> to write notes for it.
              </p></div>
            )}
            <TranscriptHead lines={m.lines} name={name} voices={voices} />
            <Transcript lines={m.lines} query={query} name={name} voices={voices} onName={onName} />
          </>
        )}
      </div>
    </div>
  );
}
