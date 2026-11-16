# 04: Speakers strip on live and past Meetings

**What to build:** A Speakers strip at the top of the live Meeting and of each past Meeting on the Meetings page. It lists every Voice that spoke in that Meeting with how long they talked and 2–3 sample lines, so Milan can work out who each one is without audio. Each entry has the same name box as the transcript chip, now with autocomplete from People pages. Typing a new name creates the Person.

**Blocked by:** 03 (Name a Voice from a transcript line)

**Status:** done

- [x] The strip lists every Voice that spoke in the Meeting, named and unnamed, with talk time and 2–3 representative sample lines
- [x] The name box autocompletes from existing People pages, and choosing one links to that page
- [x] Typing a name that isn't a Person creates the Person page
- [x] The strip updates live as new Voices speak and after any naming
- [x] Tested: the strip is computed in the browser (`format.speakerStats`, covered by `npm test`), so there is no endpoint; `GET /people` for autocomplete has an app test

## Comments

Done. The strip is computed client-side from the lines the page already has, so it updates live with no polling. Talk time is estimated from words (~150 a minute) because lines carry no durations. Samples are the up-to-3 longest lines (4+ words when there are any), in the order they were said. Legacy `Other` and unlabeled lines aren't listed since they aren't Voices. A name typed all lowercase gets title-cased for a new Person page. Added `web/tests/format.test.js` (`npm test`, node's built-in runner). Checked in Chrome: "priya shah" linked to the existing `people/priya-shah` page, and the card, "not named yet" count and transcript all updated.
