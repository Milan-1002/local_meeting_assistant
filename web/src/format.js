import { useEffect, useState } from "react";

export const money = (c) => "$" + c.toFixed(c < 1 ? 3 : 2);
export const initial = (name) => (name || "?").trim()[0].toUpperCase();
const secs = (clock) => clock.split(":").reduce((a, b) => a * 60 + Number(b), 0);

// "2026-10-22 13:02" or a Date -> parts for headers and meta lines
export function when(d) {
  const date = typeof d === "string" ? new Date(d.replace(" ", "T")) : d;
  return {
    day: date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }),
    time: date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }),
  };
}

// Consecutive lines become one paragraph until the speaker changes, a pause of 20 s, or 6 lines.
export function paragraphs(lines) {
  const out = [];
  let prev = null;
  for (const l of lines) {
    const t = secs(l.clock), cur = out[out.length - 1];
    const who = l.speaker || "";
    if (!cur || who !== cur.speaker || t - prev > 20 || cur.lines.length >= 6)
      out.push({ start: l.clock, end: l.clock, speaker: who, lines: [l] });
    else { cur.lines.push(l); cur.end = l.clock; }
    prev = t;
  }
  return out;
}

// Who said a line. Mirrors speakers.py: a line's speaker is "me", a remembered Voice ("s3"),
// "them" (older meetings), or "" when unknown. `voices` maps named Voices to their Person:
// { s3: { name: "Priya", me: false } } (from the server's "voices" event).
export const voiceNumber = (speaker) => {
  const m = /^s([1-9]\d*)$/.exec(speaker || "");
  return m ? Number(m[1]) : null;
};
export const isMe = (speaker, voices = {}) => speaker === "me" || !!voices[speaker]?.me;
// As shown and copied ("Milan", "Priya", "Speaker 3", "Other"); "" when unknown.
export const speakerName = (speaker, name, voices = {}) => {
  if (isMe(speaker, voices)) return (name || "Me").split(" ")[0];
  if (voices[speaker]) return voices[speaker].name;
  const n = voiceNumber(speaker);
  return n ? `Speaker ${n}` : speaker === "them" ? "Other" : "";
};
// As shown on a transcript paragraph ("You", "Priya", "Speaker 3", "Other"); "" when unknown.
export const speakerChip = (speaker, voices = {}) => (isMe(speaker, voices) ? "You" : speakerName(speaker, "", voices));

// Lines -> "[00:12] Milan: text", for pasting the whole transcript into another chat.
export function transcriptText(lines, name, voices = {}) {
  return lines.map((l) => {
    const who = speakerName(l.speaker, name, voices);
    return `[${l.clock}] ${who ? who + ": " : ""}${l.text}`;
  }).join("\n");
}

// Every Voice that spoke in these lines, most talkative first, for the Speakers strip:
// { speaker, lines, words, samples: [line, …] }. Unlabeled and old "Other" lines aren't Voices.
// Samples are the (up to) 3 longest lines, shown in the order they were said.
export function speakerStats(lines) {
  const by = new Map();
  for (const l of lines) {
    if (!l.speaker || l.speaker === "them") continue;
    const e = by.get(l.speaker) || { speaker: l.speaker, lines: 0, words: 0, all: [] };
    e.lines += 1;
    e.words += l.text.split(/\s+/).filter(Boolean).length;
    e.all.push(l);
    by.set(l.speaker, e);
  }
  const words = (l) => l.text.split(/\s+/).filter(Boolean).length;
  return [...by.values()]
    .map(({ all, ...e }) => {
      const long = all.filter((l) => words(l) >= 4);
      const pick = (long.length ? long : all).slice().sort((a, b) => words(b) - words(a)).slice(0, 3);
      return { ...e, samples: all.filter((l) => pick.includes(l)) };
    })
    .sort((a, b) => b.words - a.words);
}

// Roughly how long someone talked, from their word count (~150 words a minute).
export const talkTime = (words) => (words < 150 ? "under a minute" : `about ${Math.round(words / 150)} min`);

export function useElapsed(started, stopped) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (stopped) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [stopped]);
  if (!started) return "00:00";
  const total = Math.max(0, Math.floor(now / 1000 - started));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function useCopy() {
  const [copied, setCopied] = useState(null);
  return [copied, (key, text) => {
    navigator.clipboard?.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied((c) => (c === key ? null : c)), 1200);
  }];
}
