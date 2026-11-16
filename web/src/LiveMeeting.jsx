import { useLayoutEffect, useRef, useState } from "react";
import Speakers from "./Speakers.jsx";
import Transcript, { TranscriptHead } from "./Transcript.jsx";
import { initial, when } from "./format.js";
import * as I from "./icons.jsx";

function Overview({ analysis, auto, onRefresh }) {
  const [more, setMore] = useState(false);
  const [asked, setAsked] = useState(false);
  const refresh = () => { setAsked(true); onRefresh(); setTimeout(() => setAsked(false), 4000); };
  const extra = [["Decisions", analysis?.decisions], ["Open issues", analysis?.open_issues]].filter(([, x]) => x?.length);

  return (
    <div className="card overview">
      <div className="card-row">
        <h3>Overview{analysis?.clock && <span className="muted"> · updated {analysis.clock}</span>}</h3>
        <button className="link" onClick={refresh}><I.Refresh size={14} /> {asked ? "Updating…" : "Refresh"}</button>
      </div>
      {analysis?.summary?.length ? (
        <>
          <p className={`overview-text ${more ? "" : "clamp"}`}>{analysis.summary.join(" ")}</p>
          {more && extra.map(([t, items]) => (
            <div key={t} className="overview-sec"><h4>{t}</h4><ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          ))}
          <button className="show-more" onClick={() => setMore(!more)}>{more ? "Show less" : "Show more"}</button>
        </>
      ) : (
        <p className="overview-text muted">
          {auto ? "A summary appears here after a few minutes of conversation." : "No summary yet. Click Refresh to write one from the transcript so far."}
        </p>
      )}
    </div>
  );
}

export default function LiveMeeting({ s, act, query, elapsed, name, onAnswer, onQuestions }) {
  const box = useRef(null);
  const stick = useRef(true);   // follow new lines until the user scrolls up
  const [away, setAway] = useState(false);
  const c = s.config || {};
  const started = c.started ? when(new Date(c.started * 1000)) : when(new Date());
  const lines = s.segments;

  useLayoutEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length, s.partial]);

  const onScroll = () => {
    const b = box.current;
    stick.current = b.scrollHeight - b.scrollTop - b.clientHeight < 120;
    setAway(!stick.current);
  };
  const jump = () => { stick.current = true; setAway(false); box.current.scrollTop = box.current.scrollHeight; };

  return (
    <div className="page" ref={box} onScroll={onScroll}>
      <div className="page-inner">
        <div className="day-head">{started.day}</div>

        <div className="meeting-head">
          <div className="avatar">{initial(name)}</div>
          <div>
            <h2>{s.analysis?.current_topic || "Live meeting"}</h2>
            <div className="meta">
              {started.time} · {elapsed} · {name}
              {c.ended ? <span className="tag">Ended</span>
                : c.paused ? <span className="tag">Paused</span>
                : <span className="tag rec"><i />Recording</span>}
            </div>
          </div>
        </div>

        <Speakers lines={lines} voices={s.voices} name={name} onName={act.nameVoice} onForget={act.forgetSpeaker} />
        <Overview analysis={s.analysis} auto={c.auto} onRefresh={act.refreshNotes} />

        <TranscriptHead lines={lines} name={name} voices={s.voices} />
        {!lines.length && !s.partial ? (
          <div className="listening">
            <div className="bars"><i /><i /><i /><i /><i /></div>
            <p><b>{c.paused ? "Paused." : "Listening…"}</b> {c.paused ? "Click Record to pick up where you left off." : "Words appear here as people talk."}</p>
            <p className="muted">Click any sentence to draft an answer or get questions to ask.</p>
          </div>
        ) : (
          <Transcript lines={lines} partial={s.partial} flags={s.flags} query={query} name={name}
                      voices={s.voices} onName={act.nameVoice} onAnswer={onAnswer} onQuestions={onQuestions} />
        )}
      </div>
      {away && <button className="jump" onClick={jump}><I.ArrowDown size={14} /> Jump to live</button>}
    </div>
  );
}
