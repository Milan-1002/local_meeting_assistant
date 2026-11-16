import { memo, useMemo, useState } from "react";
import { SpeakerChip } from "./Transcript.jsx";
import { initial, isMe, speakerStats, talkTime, voiceNumber } from "./format.js";

// Who spoke in this Meeting: one card per Voice with how much they talked and a few of their lines,
// so you can tell who each Speaker is and name them (the name box autocompletes from People pages).
// Forget a Voice's print: two clicks, like End. Past lines keep their name; the voice comes back as a new Speaker.
function Forget({ n, onForget }) {
  const [armed, setArmed] = useState(false);
  const [note, setNote] = useState("");
  if (note) return <span className="forget-note">{note}</span>;
  const go = async () => {
    const err = await onForget(n);
    setNote(err || "Voice print deleted. Past lines keep the name; this voice will show up as a new speaker.");
  };
  return armed
    ? <span className="forget-row"><button className="link danger" onClick={go}>Delete voice print</button>
        <button className="link" onClick={() => setArmed(false)}>Keep</button></span>
    : <button className="link subtle" onClick={() => setArmed(true)} title="Delete this voice print, for example if they asked">Forget voice</button>;
}

function Speakers({ lines, voices = {}, name, onName, onForget }) {
  const stats = useMemo(() => speakerStats(lines), [lines]);
  if (!stats.length) return null;
  const unnamed = stats.filter((s) => voiceNumber(s.speaker) && !voices[s.speaker]).length;
  return (
    <div className="card speakers">
      <div className="card-row">
        <h3>Speakers{unnamed > 0 && <span className="muted"> · {unnamed} not named yet</span>}</h3>
      </div>
      <div className="speaker-list">
        {stats.map((s) => {
          const me = isMe(s.speaker, voices);
          const who = me ? name : voices[s.speaker]?.name;
          return (
            <div key={s.speaker} className="speaker-card">
              <div className="speaker-top">
                {me ? <div className="avatar sm">{initial(name)}</div>
                  : <div className="tavatar them voice sm">{who ? initial(who) : voiceNumber(s.speaker)}</div>}
                <div className="speaker-who">
                  <SpeakerChip speaker={s.speaker} voices={voices} onName={onName} />
                  <span className="muted">{talkTime(s.words)} · {s.lines} {s.lines === 1 ? "line" : "lines"}</span>
                </div>
              </div>
              <ul className="speaker-samples">
                {s.samples.map((l) => <li key={l.id}><span className="tclock">{l.clock}</span> {l.text}</li>)}
              </ul>
              {onForget && voiceNumber(s.speaker) && <Forget n={voiceNumber(s.speaker)} onForget={onForget} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default memo(Speakers);
