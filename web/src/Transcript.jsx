import { memo, useEffect, useMemo, useRef, useState } from "react";
import { initial, isMe, paragraphs, speakerChip, transcriptText, useCopy, voiceNumber } from "./format.js";
import * as I from "./icons.jsx";

const NO_VOICES = {};
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function Highlight({ text, query }) {
  if (!query) return text;
  const parts = text.split(new RegExp(`(${escape(query)})`, "ig"));
  return parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : p));
}

function Avatar({ speaker, name, voices }) {
  if (isMe(speaker, voices)) return <div className="avatar">{initial(name)}</div>;
  if (voices[speaker]) return <div className="tavatar them voice">{initial(voices[speaker].name)}</div>;
  if (voiceNumber(speaker)) return <div className="tavatar them voice">{voiceNumber(speaker)}</div>;
  if (speaker) return <div className="tavatar them"><I.Users size={17} /></div>;
  return <div className="tavatar"><I.Mic size={15} /></div>;
}

// One paragraph. Memoized: while someone talks, only the last paragraph (with the live draft) redraws,
// so a two-hour transcript stays smooth.
// The speaker's name on a paragraph. For a remembered Voice it's a button that opens a name box.
export function SpeakerChip({ speaker, voices, onName }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const label = speakerChip(speaker, voices);
  const n = voiceNumber(speaker);
  if (!label) return null;
  const cls = isMe(speaker, voices) ? "me" : "them";
  if (!n || !onName) return <b className={cls}>{label}</b>;
  if (!editing)
    return (
      <button className={`speaker-chip ${cls}`} title={voices[speaker] ? "Wrong person? Click to rename this voice" : "Who is this? Click to name this voice"}
              onClick={() => { setText(voices[speaker]?.name || ""); setError(""); setEditing(true); }}>
        {label}
      </button>
    );
  const save = async () => {
    const err = await onName(n, text);
    if (err) setError(err); else setEditing(false);
  };
  return (
    <span className="name-box">
      <input autoFocus value={text} placeholder={`Who is Speaker ${n}?`} aria-label={`Name Speaker ${n}`} list="people-names"
             onChange={(e) => setText(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
      <button className="pill sm primary" onClick={save} disabled={!text.trim()}>Save</button>
      <button className="pill sm ghost" onClick={() => setEditing(false)}>Cancel</button>
      {error && <span className="voice-error">{error}</span>}
    </span>
  );
}

const Block = memo(function Block({ b, flags, query, sel, partial, live, name, voices, canName, on }) {
  const chosen = b.lines.find((l) => l.id === sel);
  return (
    <div className="tblock">
      <Avatar speaker={b.speaker} name={name} voices={voices} />
      <div className="tbody">
        <div className="tmeta">
          <SpeakerChip speaker={b.speaker} voices={voices} onName={canName ? on.name : null} />
          <span className="tclock">{b.start}</span>
        </div>
        <p className="ttext">
          {b.lines.map((l, j) => {
            const f = flags[l.id]?.state;
            return (
              <span key={l.id ?? j} title={flags[l.id]?.reason || undefined}
                    className={`sent ${live ? "pick" : ""} ${f ? "f-" + f : ""} ${sel === l.id ? "sel" : ""}`}
                    onClick={live ? () => on.select(sel === l.id ? null : l.id) : undefined}>
                <Highlight text={l.text} query={query} />
                {f === "answered" && <I.Check size={12} />}
                {" "}
              </span>
            );
          })}
          {partial && <span className="sent draft">{partial}<i className="caret" /></span>}
        </p>
        {chosen && (
          <div className="sent-menu">
            <button onClick={() => { on.answer(chosen.id); on.select(null); }}><I.Sparkle size={14} /> Answer this</button>
            <button onClick={() => { on.questions(chosen.id); on.select(null); }}><I.Help size={14} /> Suggest questions</button>
          </div>
        )}
      </div>
    </div>
  );
}, (a, b) =>
  a.b.lines.length === b.b.lines.length && a.b.lines[0]?.id === b.b.lines[0]?.id && a.b.start === b.b.start &&
  a.flagKey === b.flagKey && a.query === b.query && a.sel === b.sel && a.partial === b.partial &&
  a.live === b.live && a.name === b.name && a.voices === b.voices && a.canName === b.canName);

// "Transcript" heading with a button that copies every line, to paste into another chat.
export function TranscriptHead({ lines, name, voices }) {
  const [copied, copy] = useCopy();
  return (
    <div className="section-head row">
      Transcript
      {lines.length > 0 && (
        <button className={`pill sm ghost ${copied ? "done" : ""}`} title="Copy the whole transcript"
                onClick={() => copy(1, transcriptText(lines, name, voices))}>
          {copied ? <><I.Check size={13} /> Copied {lines.length} lines</> : <><I.Copy size={13} /> Copy transcript</>}
        </button>
      )}
    </div>
  );
}

// Otter-style paragraphs. With onAnswer set, clicking a sentence offers Answer / Questions for it.
export default function Transcript({ lines, partial = "", flags = {}, query = "", name = "", voices = NO_VOICES, onName,
                                     onAnswer, onQuestions }) {
  const [sel, setSel] = useState(null);
  const blocks = useMemo(() => paragraphs(lines), [lines]);
  const live = onAnswer != null;
  // Stable handlers for the memoized paragraphs; they always call the latest props.
  const latest = useRef({});
  latest.current = { onAnswer, onQuestions, onName };
  const on = useMemo(() => ({
    select: setSel,
    answer: (id) => latest.current.onAnswer(id),
    questions: (id) => latest.current.onQuestions(id),
    name: (n, text) => latest.current.onName(n, text),
  }), []);

  useEffect(() => {
    if (sel == null) return;
    const off = (e) => !e.target.closest(".sent, .sent-menu") && setSel(null);
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [sel]);

  if (!blocks.length && !partial) return null;
  const shown = blocks.length ? blocks : [{ start: "now", speaker: "", lines: [] }];

  return (
    <div className="transcript">
      {shown.map((b, i) => {
        const inBlock = b.lines.some((l) => l.id === sel) ? sel : null;
        return (
          <Block key={`${b.start}-${b.lines[0]?.id ?? i}`} b={b} flags={flags} query={query} sel={inBlock} name={name}
                 voices={voices} canName={onName != null}
                 flagKey={b.lines.map((l) => flags[l.id]?.state || "").join()} live={live} on={on}
                 partial={i === shown.length - 1 ? partial : ""} />
        );
      })}
    </div>
  );
}
