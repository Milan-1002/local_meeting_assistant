import { useEffect, useReducer } from "react";

export const post = (url, body) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

const initial = {
  connected: false,
  config: null,
  status: "connecting…",
  segments: [],
  partial: "",
  flags: {},          // segment id -> { state, reason }
  pending: null,      // answer being drafted
  answers: [],
  suggestions: [],
  questionsBusy: false,
  questionsNote: "",  // "Nothing worth asking yet" etc.
  analysis: null,
  usage: null,
  kb: null,
  brief: null,        // "Brief me": { state: writing|ready|failed, who, open_threads, recent_decisions, watch_for }
  weekly: null,       // latest weekly-review event: { state: writing|ready|failed, week }
  voices: {},         // named Voices: { s3: { name, me, person } }
  people: [],         // names for the Speaker name box: [{ path, name }]
};

function reducer(s, { kind, data }) {
  switch (kind) {
    case "open": return { ...s, connected: true, status: s.config?.paused ? "paused" : "listening" };
    case "error": return { ...s, connected: false, status: "disconnected – retrying" };
    case "config":
      // A reconnect replays everything, so start from a clean slate.
      return { ...initial, connected: s.connected, status: data.paused ? "paused" : s.status,
               config: data, kb: data.kb, usage: s.usage, weekly: s.weekly, voices: data.voices || {}, people: s.people };
    case "segment":
      if (s.segments.some((x) => x.id === data.id)) return s;
      return { ...s, segments: [...s.segments, data], partial: "" };
    case "partial": return { ...s, partial: data.text || "" };
    case "flag": return { ...s, flags: { ...s.flags, [data.id]: data } };
    case "answer_pending": return { ...s, pending: data };
    case "answer":
      if (s.answers.some((a) => a.id === data.id)) return { ...s, pending: null };
      return { ...s, answers: [...s.answers, data], pending: null };
    case "answer_failed": return { ...s, pending: null };
    case "questions_pending": return { ...s, questionsBusy: true, questionsNote: "" };
    case "questions": {
      const list = data.replay ? [] : [...s.suggestions];
      for (const q of data.items) if (!list.some((x) => x.id === q.id)) list.push(q);
      const note = !data.items.length && !data.replay ? (data.error ? "Couldn't suggest — try again" : "Nothing worth asking yet") : "";
      const config = data.detected_type ? { ...s.config, detected_type: data.detected_type } : s.config;
      return { ...s, config, suggestions: list, questionsBusy: false, questionsNote: note };
    }
    case "questions_idle": return { ...s, questionsBusy: false };
    case "clear_note": return { ...s, questionsNote: "" };
    case "analysis": return { ...s, analysis: data };
    case "status": return { ...s, status: data };
    case "usage": return { ...s, usage: data };
    case "weekly": return { ...s, weekly: data };
    case "brief": return { ...s, brief: data.state === "failed" && s.brief?.state === "ready" ? { ...s.brief, error: data.error } : data };
    case "kb": return { ...s, kb: { ...s.kb, ...data } };
    case "voice": return { ...s, config: { ...s.config, voice: data } };
    case "voices": return { ...s, voices: data };
    case "people": return { ...s, people: data };
    case "local_config": return { ...s, config: { ...s.config, ...data } };
    default: return s;
  }
}

export function useMeeting() {
  const [state, dispatch] = useReducer(reducer, initial);

  useEffect(() => {
    const es = new EventSource("/events");
    es.onopen = () => dispatch({ kind: "open" });
    es.onerror = () => dispatch({ kind: "error" });
    es.onmessage = (e) => dispatch(JSON.parse(e.data));
    return () => es.close();
  }, []);

  const loadPeople = () => fetch("/people").then((r) => r.json()).then((data) => dispatch({ kind: "people", data })).catch(() => {});
  useEffect(() => { loadPeople(); }, []);

  useEffect(() => {
    if (!state.questionsNote) return;
    const t = setTimeout(() => dispatch({ kind: "clear_note" }), 4000);
    return () => clearTimeout(t);
  }, [state.questionsNote]);

  const actions = {
    answerLine: (segment_id) => post("/answer", { segment_id }),
    answerQuestion: (question) => post("/answer", { question }),
    redo: (a, style) => post("/answer", { question: a.question, segment_id: a.segment_id, style, previous: a.answer }),
    async suggestQuestions(body) {
      dispatch({ kind: "questions_pending" });
      const r = await post("/questions", body);
      if (!(await r.json()).ok) dispatch({ kind: "questions_idle" });
    },
    async askPrivately(q, history) {
      const r = await post("/ask", { q, history });
      return (await r.json()).answer;
    },
    feedback: (body) => post("/feedback", body),
    refreshNotes: () => post("/summarize"),
    addLine: (text) => post("/segment", { text }),
    setPaused(paused) {
      dispatch({ kind: "local_config", data: { paused } });
      dispatch({ kind: "status", data: paused ? "paused" : "listening" });
      post("/pause", { paused });
    },
    settings(body) {
      dispatch({ kind: "local_config", data: body });
      post("/settings", body);
    },
    // Resolves to an error message, or "" when the server took it.
    async recordVoice() {
      try {
        const r = await post("/voice/enroll");
        if (!r.ok) return `The server didn't accept it (HTTP ${r.status}). Restart copilot.py and try again.`;
        const d = await r.json();
        return d.ok ? "" : d.error || "Couldn't start recording.";
      } catch {
        return "Can't reach the server. Is copilot.py running?";
      }
    },
    cancelVoice: () => post("/voice/cancel"),
    newMeeting: () => post("/new"),
    // Resolves to an error message, or "" when the change was saved.
    async editTypes(body) {
      try {
        const d = await (await post("/meeting_types", body)).json();
        if (d.config) dispatch({ kind: "local_config", data: d.config });
        return d.ok ? "" : d.error || "Couldn't save that.";
      } catch {
        return "Can't reach the server. Is copilot.py running?";
      }
    },
    brief: (focus) => { dispatch({ kind: "brief", data: { state: "writing" } }); post("/brief", focus ? { focus } : {}); },
    forgetVoice: () => post("/voice/forget"),
    // Delete Speaker n's voice print. Resolves to an error message, or "" when done.
    async forgetSpeaker(voice) {
      try {
        const d = await (await post("/voices/forget", { voice })).json();
        return d.ok ? "" : d.error || "Couldn't forget that voice.";
      } catch {
        return "Can't reach the server. Is copilot.py running?";
      }
    },
    async recompile() {
      dispatch({ kind: "kb", data: { state: "compiling" } });
      try {
        const d = await (await post("/recompile")).json();
        if (!d.ok || !d.meetings) dispatch({ kind: "kb", data: { state: "done" } });  // nothing started
      } catch {
        dispatch({ kind: "kb", data: { state: "done" } });
      }
    },
    // Link Voice n to the Person called `name`. Resolves to an error message, or "" when saved.
    async nameVoice(voice, name) {
      try {
        const d = await (await post("/voices/name", { voice, name })).json();
        if (d.voices) dispatch({ kind: "voices", data: d.voices });
        if (d.ok) loadPeople();  // naming may have created a Person page
        return d.ok ? "" : d.error || "Couldn't save that.";
      } catch {
        return "Can't reach the server. Is copilot.py running?";
      }
    },
    end() {
      dispatch({ kind: "local_config", data: { ended: true, paused: true } });
      post("/end");
    },
  };

  return [state, actions];
}
