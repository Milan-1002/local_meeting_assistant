// Run with `npm test` (node's built-in test runner; no extra packages).
import assert from "node:assert/strict";
import { test } from "node:test";
import { isMe, speakerChip, speakerName, speakerStats, talkTime, transcriptText } from "../src/format.js";

const line = (id, speaker, text, clock = `00:${String(id).padStart(2, "0")}`) => ({ id, speaker, text, clock });

test("speakerStats lists each Voice once, most talkative first", () => {
  const lines = [
    line(1, "s3", "Can we move the demo to Thursday please"),
    line(2, "me", "Sure, Thursday works"),
    line(3, "s4", "ok"),
    line(4, "s3", "Staging is not ready and the data load is still running"),
    line(5, "them", "an old Other line"),
    line(6, "", "unlabeled"),
    line(7, "s3", "yes"),
  ];
  const stats = speakerStats(lines);
  assert.deepEqual(stats.map((s) => s.speaker), ["s3", "me", "s4"]);
  assert.equal(stats[0].lines, 3);
  // samples skip very short lines when longer ones exist, and keep the order they were said
  assert.deepEqual(stats[0].samples.map((l) => l.id), [1, 4]);
  // someone who only said short things still gets a sample
  assert.deepEqual(stats[2].samples.map((l) => l.id), [3]);
});

test("speakerStats keeps at most 3 samples, the longest", () => {
  const lines = [4, 9, 5, 12, 6].map((n, i) => line(i, "s2", Array(n).fill("word").join(" ")));
  assert.deepEqual(speakerStats(lines)[0].samples.map((l) => l.id), [1, 3, 4]);
});

test("names come from the voices map", () => {
  const voices = { s3: { name: "Priya", me: false }, s5: { name: "Milan", me: true } };
  assert.equal(speakerName("s3", "Milan Sunar", voices), "Priya");
  assert.equal(speakerName("s4", "Milan Sunar", voices), "Speaker 4");
  assert.equal(speakerChip("s5", voices), "You");
  assert.ok(isMe("s5", voices) && isMe("me") && !isMe("s3", voices));
  assert.equal(transcriptText([line(1, "s3", "hi")], "Milan", voices), "[00:01] Priya: hi");
});

test("talkTime", () => {
  assert.equal(talkTime(40), "under a minute");
  assert.equal(talkTime(450), "about 3 min");
});
