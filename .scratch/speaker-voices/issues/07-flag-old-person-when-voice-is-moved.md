# 07: Flag the old Person when a Voice is moved

**What to build:** When Milan moves a Voice from one Person to another (he named Speaker 3 "Priya", compiled, then realised it was Bob), the old Person's page is recorded as needing review. `kb.py lint` reports the facts on that page that cite the affected Meetings, so Milan (or `lint --fix`) can check them. Nothing is ever deleted automatically, because some of those facts may really be Priya's.

**Blocked by:** 06 (Recompile after naming)

**Status:** done

- [x] Moving a Voice from Priya to Bob marks the affected Meetings for recompile (as in 06) and records Priya's page for review
- [x] App test: `lint` reports the facts on Priya's page that cite the affected Meetings, and none that cite other Meetings
- [x] No fact is removed from Priya's page by the move or by a recompile
- [x] Naming a previously unnamed Voice does not flag any page

## Comments

Done. `kb.flag_review` writes `needs_review` (the reason) and `review_meetings` (compiled Meetings with that Voice) into the old Person's frontmatter, or into `me.md` when the Voice was Me. `lint` → `check_after_voice_moved` lists every line on the page citing those Meetings. Nothing is deleted; the user removes the two frontmatter lines once checked. Tests: `tests/test_moved_voice.py`.
