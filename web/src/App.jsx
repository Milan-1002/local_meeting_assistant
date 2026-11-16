import { useEffect, useRef, useState } from "react";
import { useMeeting } from "./useMeeting.js";
import { useResizable } from "./useResizable.js";
import { initial, money, useElapsed } from "./format.js";
import LiveMeeting from "./LiveMeeting.jsx";
import Meetings from "./Meetings.jsx";
import RightPanel from "./RightPanel.jsx";
import Settings from "./Settings.jsx";
import Weekly from "./Weekly.jsx";
import * as I from "./icons.jsx";

// Ends the current meeting (saved like End) and starts a fresh one. Asks first when there's something to end.
function NewMeetingButton({ s, act, className }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const c = s.config || {};
  const needsConfirm = s.segments.length > 0 && !c.ended;
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  const click = async () => {
    if (needsConfirm && !armed) return setArmed(true);
    setArmed(false); setBusy(true);
    try { await act.newMeeting(); } finally { setBusy(false); }
  };
  return (
    <button className={`${className} ${armed ? "armed" : ""}`} onClick={click} disabled={busy}
            title="Save this meeting and start a new one">
      <I.Plus size={17} /> {busy ? "Saving & starting…" : armed ? "End this one & start new?" : "New meeting"}
    </button>
  );
}

function Sidebar({ s, act, name, view, go, openChat, openSettings, recording }) {
  const c = s.config || {};
  return (
    <aside className="sidebar">
      <div className="brand"><span className="logo"><I.Sparkle size={16} /></span> Copilot</div>
      <NewMeetingButton s={s} act={act} className="pill primary new-meeting" />

      <div className="user-card">
        <div className="user-row">
          <div className="avatar sm">{initial(name)}</div>
          <div className="user-text"><b>{name}</b><span>On-device · private</span></div>
        </div>
        <button className="user-link" onClick={openSettings}><I.Gear size={16} /> Settings</button>
      </div>

      <nav className="nav">
        <button className={view === "live" ? "on" : ""} onClick={() => go("live")}>
          <I.Mic size={18} /> Live meeting {recording && <i className="live-dot" />}
        </button>
        <button className={view === "meetings" ? "on" : ""} onClick={() => go("meetings")}>
          <I.List size={18} /> Meetings
        </button>
        <button className={view === "weekly" ? "on" : ""} onClick={() => go("weekly")}>
          <I.Calendar size={18} /> Weekly review {s.weekly?.state === "ready" && view !== "weekly" && <i className="new-dot" />}
        </button>
        <button onClick={openChat}><I.Chat size={18} /> AI Chat</button>
      </nav>

      <div className="grow" />

      {s.kb && (
        <div className="side-card">
          <b>Knowledge base</b>
          <span>{s.kb.pages} pages{s.kb.pending ? ` · ${s.kb.pending} meeting${s.kb.pending > 1 ? "s" : ""} to add` : ""}</span>
          <a href={`obsidian://open?path=${encodeURIComponent(s.kb.vault)}`}>Open in Obsidian <I.ChevronR size={14} /></a>
        </div>
      )}
      <div className="side-card">
        <div className="usage-row"><b>{c.auto ? "Automatic" : "Manual"}</b><span>{s.usage ? money(s.usage.cost) : "$0.00"}</span></div>
        <span>{c.auto ? "AI answers on its own." : "AI runs only when you click."}{s.usage ? ` ${s.usage.calls} calls this meeting.` : ""}</span>
        <button className="pill outline" onClick={openSettings}>{c.auto ? "Switch to manual" : "Change mode"}</button>
      </div>
    </aside>
  );
}

export default function App() {
  const [s, act] = useMeeting();
  const [view, setView] = useState("live");             // live | meetings | weekly
  const [openId, setOpenId] = useState(null);           // past meeting being read
  const [tab, setTab] = useState("copilot");            // right panel tab
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 1100);  // overlays the page when narrow
  const [navOpen, setNavOpen] = useState(false);        // sidebar on narrow screens
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [endArmed, setEndArmed] = useState(false);
  const [query, setQuery] = useState("");
  const [chat, setChat] = useState([]);
  const search = useRef(null);
  const [sideW, sideHandle] = useResizable("sideWidth", { initial: 272, min: 200, max: 420, side: "left" });
  const [panelW, panelHandle] = useResizable("panelWidth", { initial: 400, min: 300, max: 760, side: "right" });
  const c = s.config || {};
  const name = c.name || "Me";
  const elapsed = useElapsed(c.started, c.ended);
  const recording = s.connected && !c.paused && !c.ended;
  const problem = /error|disconnected/i.test(s.status);

  const showPanel = (t) => { setTab(t); setPanelOpen(true); };
  const go = (v) => { setView(v); setOpenId(null); setNavOpen(false); setQuery(""); };

  // A new meeting started (here or in another tab): drop everything tied to the old one.
  useEffect(() => {
    if (!c.meeting_id) return;
    setChat([]); setQuery(""); setOpenId(null); setView("live"); setEndArmed(false);
  }, [c.meeting_id]);

  // A new answer: make sure it's visible.
  useEffect(() => { if (s.pending) showPanel("copilot"); }, [s.pending]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") { setSettingsOpen(false); setNavOpen(false); }
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); search.current?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!endArmed) return;
    const t = setTimeout(() => setEndArmed(false), 3000);
    return () => clearTimeout(t);
  }, [endArmed]);

  const ask = async (q) => {
    showPanel("chat");
    const history = chat.filter((m) => m.text);
    setChat((m) => [...m, { role: "me", text: q }, { role: "ai", text: null }]);
    const answer = await act.askPrivately(q, history);
    setChat((m) => [...m.slice(0, -1), { role: "ai", text: answer }]);
  };
  const suggest = (body) => { showPanel("copilot"); act.suggestQuestions(body); };

  return (
    <div className={`app ${panelOpen ? "" : "no-panel"} ${navOpen ? "nav-open" : ""}`}
         style={{ "--side-w": sideW + "px", "--panel-w": panelW + "px" }}>
      <Sidebar s={s} act={act} name={name} view={view} go={go} recording={recording}
               openChat={() => { showPanel("chat"); setNavOpen(false); }}
               openSettings={() => { setSettingsOpen(true); setNavOpen(false); }} />
      <div className="scrim" onClick={() => setNavOpen(false)} />

      <main className="center">
        <div className="resizer left" {...sideHandle} />
        {panelOpen && <div className="resizer right" {...panelHandle} />}
        <header className="topbar">
          <button className="icon-btn menu" onClick={() => setNavOpen(true)} title="Menu"><I.Menu size={18} /></button>
          <label className="search">
            <I.Search size={17} />
            <input ref={search} value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder={view === "live" ? "Ask or search this meeting" : "Ask or search meetings"}
                   onKeyDown={(e) => { if (e.key === "Enter" && query.trim()) { ask(query.trim()); setQuery(""); } }} />
            <kbd>⌘K</kbd>
          </label>
          <span className="grow" />
          {problem && <span className="status-err" title={s.status}>{s.status}</span>}
          {!c.ended && (c.paused
            ? <button className="pill primary lg" onClick={() => { act.setPaused(false); go("live"); }}><I.Mic size={17} /> Record</button>
            : <button className="pill soft lg" onClick={() => act.setPaused(true)}><i className="rec-dot" /> {elapsed} <span className="sep" /> <I.Pause size={15} /> Pause</button>)}
          {c.ended
            ? <NewMeetingButton s={s} act={act} className="pill primary lg" />
            : <button className={`pill lg ${endArmed ? "danger-solid" : "soft"}`}
                      onClick={() => (endArmed ? (setEndArmed(false), act.end()) : setEndArmed(true))}>
                <I.Stop size={15} /> {endArmed ? "Click to end" : "End"}
              </button>}
          {!panelOpen && <button className="icon-btn" onClick={() => setPanelOpen(true)} title="Show panel"><I.ChevronL size={18} /></button>}
        </header>

        {view === "live"
          ? <LiveMeeting key={c.meeting_id} s={s} act={act} query={query} elapsed={elapsed} name={name}
                         onAnswer={(id) => { showPanel("copilot"); act.answerLine(id); }}
                         onQuestions={(id) => suggest({ segment_id: id })} />
          : view === "weekly"
          ? <Weekly event={s.weekly} onOpenMeeting={(id) => { setView("meetings"); setOpenId(id); }} />
          : <Meetings name={name} query={query} openId={openId} onOpen={setOpenId} voices={s.voices} onName={act.nameVoice}
                      onForget={act.forgetSpeaker} kb={s.kb} onRecompile={act.recompile} />}
      </main>
      <datalist id="people-names">{s.people.map((p) => <option key={p.path} value={p.name} />)}</datalist>

      {panelOpen && <RightPanel tab={tab} setTab={setTab} s={s} act={act} chat={chat} onAsk={ask}
                                onSuggest={suggest} onCollapse={() => setPanelOpen(false)} />}

      {settingsOpen && <Settings config={c} kb={s.kb} usage={s.usage} onChange={act.settings} act={act}
                                 onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
