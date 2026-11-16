import { useEffect, useRef, useState } from "react";
import { useCopy } from "./format.js";
import * as I from "./icons.jsx";

function AnswerCard({ a, big, onRedo, onNotUseful }) {
  const [copied, copy] = useCopy();
  const [open, setOpen] = useState(big);
  const [flagged, setFlagged] = useState(false);
  return (
    <div className={`acard ${big ? "big" : ""} ${open ? "open" : ""}`} onClick={() => !big && setOpen(!open)}>
      <div className="acard-q">
        <span>{a.asker && <b>{a.asker}: </b>}{a.question}</span>
        <span className="acard-time">{a.clock}</span>
      </div>
      <p className="acard-a">{a.answer}</p>
      {open && (
        <>
          {a.points?.length > 0 && <ul className="points">{a.points.map((p, i) => <li key={i}>{p}</li>)}</ul>}
          <div className="row-actions" onClick={(e) => e.stopPropagation()}>
            <button className={`pill sm primary ${copied ? "done" : ""}`} onClick={() => copy(1, a.answer)}>
              {copied ? <><I.Check size={13} /> Copied</> : <><I.Copy size={13} /> Copy</>}
            </button>
            <button className="pill sm ghost" onClick={() => onRedo(a, "shorter")}>Shorter</button>
            <button className="pill sm ghost" onClick={() => onRedo(a, "different")}>Different</button>
            <button className="pill sm ghost" onClick={() => onRedo(a, "detailed")}>Longer</button>
            <span className="grow" />
            <button className="icon-btn" disabled={flagged} title={flagged ? "Noted" : "Not useful"}
                    onClick={() => { setFlagged(true); onNotUseful(a); }}>
              {flagged ? <I.Check size={14} /> : <I.Down size={14} />}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// What to know going into this meeting, from past meetings: people, open threads, decisions.
function Brief({ b, onBrief, auto }) {
  const [open, setOpen] = useState(true);
  const busy = b?.state === "writing";
  const ready = b?.state === "ready";
  const has = ready && (b.who?.length || b.open_threads?.length || b.recent_decisions?.length || b.watch_for?.length);
  const lists = ready ? [["Open threads", b.open_threads], ["Earlier decisions", b.recent_decisions], ["Watch for", b.watch_for]] : [];
  return (
    <div className="brief-sec">
      <div className="panel-title-row">
        <h3 className="panel-title">Brief</h3>
        <span className="grow" />
        {ready && <button className="link" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>}
        <button className="link" onClick={() => onBrief()} disabled={busy}>
          {busy ? "Reading your notes…" : ready ? <><I.Refresh size={13} /> Again</> : "Brief me"}
        </button>
      </div>
      {!b && <p className="panel-sub">{auto ? "Written once you describe the meeting in Settings, or click Brief me."
                                            : "Who's here, what's open with them and what was decided before."}</p>}
      {b?.state === "failed" && <div className="empty-box">{b.error || "Couldn't write the brief."}</div>}
      {busy && <div className="acard pending"><div className="skeleton"><span /><span /><span /></div></div>}
      {ready && !has && <div className="empty-box">Nothing in your past meetings about this yet.</div>}
      {ready && has && open && (
        <div className="brief">
          {b.who?.length > 0 && (
            <ul className="brief-who">
              {b.who.map((p, i) => <li key={i}><b>{p.name}</b> {p.line}</li>)}
            </ul>
          )}
          {lists.filter(([, xs]) => xs?.length).map(([label, xs]) => (
            <div key={label}>
              <h4 className="brief-h">{label}</h4>
              <ul className="brief-list">{xs.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Copilot({ s, act, auto, onSuggest }) {
  const [copied, copy] = useCopy();
  const [voted, setVoted] = useState({});
  const [text, setText] = useState("");
  const latest = s.answers[s.answers.length - 1];
  const older = s.answers.slice(0, -1).reverse();
  const qs = s.suggestions.slice(-2).reverse();
  const q = text.trim();
  const c = s.config || {};
  const type = c.meeting_type && c.meeting_type !== "auto" ? c.meeting_type : c.detected_type;
  const lens = type && (c.meeting_types?.[type] || type) + (c.meeting_type === "auto" ? " (auto-detected)" : "");
  const notUseful = (a) => act.feedback({ answer_id: a.id, question: a.question, answer: a.answer, verdict: "not useful" });

  return (
    <>
      <div className="panel-scroll">
        <Brief b={s.brief} onBrief={act.brief} auto={auto} />
        <h3 className="panel-title">Suggested answers</h3>
        <p className="panel-sub">What you could say, in your voice.</p>
        {s.pending && (
          <div className="acard big pending">
            <div className="acard-q">{s.pending.question}</div>
            <div className="skeleton"><span /><span /><span /></div>
          </div>
        )}
        {!latest && !s.pending && (
          <div className="empty-box">{auto ? "When someone asks you something, an answer shows up here."
                                            : "Click a sentence in the transcript, or type a question below."}</div>
        )}
        {latest && <AnswerCard key={latest.id} a={latest} big onRedo={act.redo} onNotUseful={notUseful} />}
        {older.map((a) => <AnswerCard key={a.id} a={a} onRedo={act.redo} onNotUseful={notUseful} />)}

        <div className="panel-title-row">
          <h3 className="panel-title">Questions to ask</h3>
          <button className="link" onClick={() => onSuggest()} disabled={s.questionsBusy}>
            {s.questionsBusy ? "Thinking…" : s.questionsNote || "Suggest"}
          </button>
        </div>
        {lens && <p className="panel-sub">Tuned for: {lens}</p>}
        {!qs.length && (
          <div className="empty-box">{auto ? `Ideas appear when someone says "any questions?" or the topic changes.`
                                           : "Click Suggest for ideas based on the conversation so far."}</div>
        )}
        {qs.map((x) => (
          <div key={x.id} className="qcard">
            <p className="qcard-text">{x.question}</p>
            <p className="qcard-why">{x.angle && <span className="angle">{x.angle}</span>}{x.why}</p>
            <div className="row-actions">
              <button className="icon-btn" onClick={() => copy("q" + x.id, x.question)} title="Copy">
                {copied === "q" + x.id ? <I.Check size={14} /> : <I.Copy size={14} />}
              </button>
              <span className="grow" />
              {["good", "bad"].map((v) => (
                <button key={v} className={`icon-btn ${voted[x.id] === v ? "on" : ""}`} disabled={!!voted[x.id]}
                        title={v === "good" ? "Good question" : "Not useful"}
                        onClick={() => { setVoted({ ...voted, [x.id]: v }); act.feedback({ kind: "question", question: x.question, why: x.why, verdict: v }); }}>
                  {v === "good" ? <I.Up size={14} /> : <I.Down size={14} />}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="panel-input">
        <div className="input-box">
          <textarea rows={2} value={text} id="composer" placeholder="Type a question to answer as you…"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (q) { act.answerQuestion(q); setText(""); } } }} />
          <div className="input-actions">
            <button className="pill sm ghost" disabled={!q} onClick={() => { onSuggest({ focus: q }); setText(""); }}><I.Help size={13} /> Questions on this</button>
            <span className="grow" />
            <button className="pill sm primary" disabled={!q} onClick={() => { act.answerQuestion(q); setText(""); }}>Answer as me <I.Send size={13} /></button>
          </div>
        </div>
      </div>
    </>
  );
}

const STARTERS = ["What have we decided so far?", "What was I asked to do?", "Summarize the last few minutes", "What did we discuss in past meetings about this?"];

function AIChat({ chat, onAsk }) {
  const [text, setText] = useState("");
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [chat]);
  const send = (q) => { if (q.trim()) { onAsk(q.trim()); setText(""); } };

  return (
    <>
      <div className="panel-scroll">
        {!chat.length && (
          <div className="chat-empty">
            <div className="chat-badge"><I.Sparkle size={20} /></div>
            <h3>Ask about your meetings</h3>
            <p>Answers use this meeting and your knowledge base. Only you see them.</p>
            <div className="starters">{STARTERS.map((x) => <button key={x} onClick={() => send(x)}>{x}</button>)}</div>
          </div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "ai" && <div className="msg-avatar"><I.Sparkle size={13} /></div>}
            <div className="bubble">{m.text ?? <span className="dots">Thinking</span>}</div>
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="panel-input">
        <div className="input-box row">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything…"
                 onKeyDown={(e) => e.key === "Enter" && send(text)} />
          <button className="send-round" disabled={!text.trim()} onClick={() => send(text)}><I.Send size={15} /></button>
        </div>
      </div>
    </>
  );
}

function ActionItems({ analysis, onRefresh }) {
  const [done, setDone] = useState({});
  const items = analysis?.action_items || [];
  return (
    <div className="panel-scroll">
      <div className="panel-title-row">
        <h3 className="panel-title">Action items</h3>
        <button className="link" onClick={onRefresh}><I.Refresh size={13} /> Refresh</button>
      </div>
      {!items.length && <div className="empty-box">Action items from this meeting show up here after notes are written.</div>}
      {items.map((x, i) => (
        <label key={i} className={`todo ${done[i] ? "done" : ""}`}>
          <input type="checkbox" checked={!!done[i]} onChange={() => setDone({ ...done, [i]: !done[i] })} />
          <span>{x}</span>
        </label>
      ))}
    </div>
  );
}

export default function RightPanel({ tab, setTab, s, act, chat, onAsk, onSuggest, onCollapse }) {
  const auto = s.config?.auto;
  const n = s.analysis?.action_items?.length || 0;
  const tabs = [["copilot", "Copilot"], ["chat", "AI Chat"], ["actions", `Action Items${n ? ` (${n})` : ""}`]];
  return (
    <aside className="panel">
      <nav className="panel-tabs">
        {tabs.map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            {label}{k === "copilot" && s.pending && <i className="dot" />}
          </button>
        ))}
        <span className="grow" />
        <button className="icon-btn" onClick={onCollapse} title="Hide panel"><I.Chevrons size={16} /></button>
      </nav>
      {tab === "copilot" && <Copilot s={s} act={act} auto={auto} onSuggest={onSuggest} />}
      {tab === "chat" && <AIChat chat={chat} onAsk={onAsk} />}
      {tab === "actions" && <ActionItems analysis={s.analysis} onRefresh={act.refreshNotes} />}
    </aside>
  );
}
