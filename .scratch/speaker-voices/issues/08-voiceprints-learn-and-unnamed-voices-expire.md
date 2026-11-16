# 08: Voiceprints learn and unnamed Voices expire

**What to build:** Recognition gets better the more Milan meets someone, without drifting. At End meeting or New meeting, each Voice's voiceprint takes a small, bounded step towards that Meeting's confident matches. Grey-zone and borrowed labels never count. An unnamed Voice not heard for 30 days is dropped so one-off guests don't pile up. Its number stays retired, and past Transcripts keep their `Speaker N:` lines.

**Blocked by:** 02 (Remembered Voices labelled Speaker N, live)

**Status:** done

- [x] Registry tests with fake vectors: after a Meeting of confident matches, the voiceprint moves towards them, but by no more than the set step
- [x] Low-confidence or borrowed labels don't change the voiceprint
- [x] With an injected clock, an unnamed Voice unheard for 30 days is dropped, and a named Voice or one heard recently is kept
- [x] After a drop, the next new voice gets a fresh number and the dropped number is never reused
- [x] Learning runs once per Meeting, at End meeting or New meeting, not per segment

## Comments

Done. Confident = score ≥ `VOICE_MATCH` + 0.10, and only for established Voices (young ones already average every match). Up to 40 prints per Voice are buffered per meeting; `Registry.end_meeting` needs at least 3 and moves the print by `LEARN_RATE` 0.10 towards their mean (checked: similarity to the old print stays above 0.97). It runs from `copilot.end_voice_meeting` at End meeting, New meeting and shutdown, which also prunes unnamed Voices unheard for 30 days. Named ones (`speakers.named_numbers`) and Me are kept, and numbers are never reused. Grey-zone and borrowed labels never pass through `identify`'s confident branch.
