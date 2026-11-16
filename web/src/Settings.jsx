import { useEffect, useRef, useState } from "react";
import * as I from "./icons.jsx";

const money = (c) => "$" + c.toFixed(c < 1 ? 3 : 2);
const LEVELS = [["low", "Only when named"], ["normal", "Normal"], ["high", "Any question"]];

const SAMPLE = "I usually work on backend services in Java and Spring Boot. Lately I've been learning how the " +
  "rebate data moves from the source feeds into Kafka and then into BigQuery for reporting. Before I build " +
  "anything, I like to understand who owns each piece and what happens when something fails.";

function VoiceSetup({ voice = {}, act }) {
  const { state, enrolled, available } = voice;
  const [error, setError] = useState("");
  const record = async () => { setError(""); setError(await act.recordVoice()); };
  if (!available) {
    return <p className="field-hint">Available when the app listens to the microphone (not in paste-only mode).</p>;
  }
  if (state === "recording" || state === "saving") {
    const pct = Math.min(100, Math.round(((voice.seconds || 0) / (voice.target || 20)) * 100));
    return (
      <div className="voice-rec">
        <p className="voice-status"><i className="rec-dot" /> {state === "saving" ? "Saving your voice…" : "Recording — read this aloud at your normal pace:"}</p>
        <blockquote>{SAMPLE}</blockquote>
        <div className="progress"><i style={{ width: pct + "%" }} /></div>
        <span className="field-hint">{voice.seconds || 0} of {voice.target || 20} seconds of speech. Only you should be talking. Nothing here goes into the transcript.</span>
        {state === "recording" && <button className="pill sm ghost" style={{ justifySelf: "start" }} onClick={act.cancelVoice}>Cancel</button>}
      </div>
    );
  }
  return (
    <>
      {(error || state === "failed") && <p className="voice-error">{error || voice.error || "Recording failed."}</p>}
      {enrolled ? (
        <div className="voice-row">
          <span className="voice-ok"><I.Check size={15} /> Voice set up</span>
          <span className="grow" />
          <button className="pill sm ghost" onClick={record}>Re-record</button>
          <button className="pill sm ghost" onClick={act.forgetVoice}>Remove</button>
        </div>
      ) : (
        <div className="voice-row">
          <button className="pill primary" onClick={record}><I.Mic size={15} /> Record my voice</button>
          <span className="field-hint">About 20 seconds, once. Stays on this Mac.</span>
        </div>
      )}
    </>
  );
}

// One meeting type: name and lens ("what good questions look like here"). Saves when a field loses focus.
function TypeRow({ k, label, lens, act, setError }) {
  const [name, setName] = useState(label);
  const [text, setText] = useState(lens);
  useEffect(() => { setName(label); setText(lens); }, [label, lens]);
  const save = async () => {
    if (name.trim() === label && text.trim() === lens) return;
    if (!name.trim()) { setName(label); return; }
    setError(await act.editTypes({ action: "update", key: k, label: name.trim(), lens: text.trim() }));
  };
  return (
    <li className="type-row">
      <div className="type-row-head">
        <input value={name} onChange={(e) => setName(e.target.value)} onBlur={save} aria-label="Type name" />
        <button className="icon-btn" title={`Remove ${label}`}
                onClick={async () => setError(await act.editTypes({ action: "remove", key: k }))}><I.Close size={14} /></button>
      </div>
      <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} onBlur={save}
                placeholder="What good questions look like in this meeting (optional)" />
    </li>
  );
}

function TypeEditor({ config, act }) {
  const [name, setName] = useState("");
  const [lens, setLens] = useState("");
  const [error, setError] = useState("");
  const types = Object.entries(config.meeting_types || {});
  const add = async () => {
    if (!name.trim()) return;
    const err = await act.editTypes({ action: "add", label: name.trim(), lens: lens.trim() });
    setError(err);
    if (!err) { setName(""); setLens(""); }
  };
  return (
    <div className="type-editor">
      {error && <p className="voice-error">{error}</p>}
      <ul className="type-list">
        {types.map(([k, label]) => (
          <TypeRow key={k} k={k} label={label} lens={config.meeting_type_lenses?.[k] || ""} act={act} setError={setError} />
        ))}
      </ul>
      <div className="type-add">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New type, e.g. Client demo"
               onKeyDown={(e) => e.key === "Enter" && add()} />
        <textarea rows={2} value={lens} onChange={(e) => setLens(e.target.value)}
                  placeholder="What good questions look like here (optional), e.g. ask about the client's success criteria and timelines" />
        <div className="voice-row">
          <button className="pill sm primary" disabled={!name.trim()} onClick={add}><I.Plus size={13} /> Add type</button>
          <span className="grow" />
          {config.meeting_types_custom && (
            <button className="pill sm ghost" onClick={async () => setError(await act.editTypes({ action: "reset" }))}>Reset to defaults</button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Settings({ config, kb, usage, onChange, act, onClose }) {
  const [context, setContext] = useState(config.context || "");
  const [editTypes, setEditTypes] = useState(false);
  const timer = useRef();
  useEffect(() => () => clearTimeout(timer.current), []);

  const typeContext = (v) => {
    setContext(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange({ context: v }), 600);
  };

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Settings" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Settings</h2>
          <button className="icon-btn" onClick={onClose} title="Close"><I.Close size={16} /></button>
        </div>

        <div className="modal-body">
          <label className="field">
            <span className="field-label">About this meeting</span>
            <textarea rows={3} value={context} onChange={(e) => typeContext(e.target.value)}
                      placeholder="e.g. Sprint planning for the payments service. I'm the backend dev." />
            <span className="field-hint">Used for every answer, and helps the transcript get names and jargon right.</span>
          </label>

          <div className="field">
            <div className="field-label-row">
              <span className="field-label">Meeting type</span>
              <button className="link" onClick={() => setEditTypes(!editTypes)}>{editTypes ? "Done" : "Edit types"}</button>
            </div>
            <select value={config.meeting_type || "auto"} onChange={(e) => onChange({ meeting_type: e.target.value })}>
              <option value="auto">
                Auto-detect{config.detected_type ? ` (looks like ${config.meeting_types?.[config.detected_type] || config.detected_type})` : ""}
              </option>
              {Object.entries(config.meeting_types || {}).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <span className="field-hint">Shapes the questions it suggests: edge cases in refinement, tradeoffs in design reviews, confirming your understanding in walkthroughs.</span>
            {editTypes && <TypeEditor config={config} act={act} />}
          </div>

          <div className="field">
            <span className="field-label">Your voice</span>
            <span className="field-hint">Lets the transcript tell your lines apart from everyone else's, so answers and questions skip what you already said.</span>
            <VoiceSetup voice={config.voice} act={act} />
          </div>

          <div className="field">
            <div className="toggle-row">
              <div>
                <span className="field-label">Automatic mode</span>
                <span className="field-hint">Answers, notes and question ideas without clicking. Off is cheapest: AI runs only when you click.</span>
              </div>
              <button className={`switch ${config.auto ? "on" : ""}`} role="switch" aria-checked={!!config.auto}
                      onClick={() => onChange({ auto: !config.auto })}><i /></button>
            </div>
            {config.auto && (
              <div className="segmented">
                {LEVELS.map(([v, label]) => (
                  <button key={v} className={config.sensitivity === v ? "on" : ""} onClick={() => onChange({ sensitivity: v })}>{label}</button>
                ))}
              </div>
            )}
          </div>

          <details className="more">
            <summary>Knowledge base, models & usage</summary>
            <dl>
              {kb && <>
                <dt>Vault</dt><dd>{kb.vault}</dd>
                <dt>Pages</dt><dd>{kb.pages}</dd>
                <dt>To compile</dt><dd>{kb.pending} meeting(s)</dd>
              </>}
              {config.models && <>
                <dt>Speech</dt><dd>{config.models.speech}</dd>
                <dt>Check</dt><dd>{config.models.triage}</dd>
                <dt>Answers</dt><dd>{config.models.main}</dd>
                <dt>Questions</dt><dd>{config.models.questions}</dd>
              </>}
              <dt>Input</dt><dd>{config.mode}</dd>
              {usage && Object.entries(usage.by_model).map(([m, x]) => (
                <span key={m} style={{ display: "contents" }}>
                  <dt>{m}</dt>
                  <dd>{x.cost !== undefined ? money(x.cost) + " · " : ""}{x.calls} calls · {x.in.toLocaleString()} in · {x.out.toLocaleString()} out</dd>
                </span>
              ))}
            </dl>
            <p className="field-hint">Open the vault in Obsidian to browse it. Edit <code>wiki/me.md</code> to teach it who you are.</p>
          </details>
        </div>
      </div>
    </div>
  );
}
