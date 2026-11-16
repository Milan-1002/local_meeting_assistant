# 06: Recompile after naming

**What to build:** Naming a Voice marks every already-compiled Meeting that Voice appears in as needing a recompile, since its Meeting note was written before the name was known. Nothing recompiles automatically, so no LLM call happens without a click. The Meetings page shows a "needs recompile" badge and a Recompile button. `kb.py compile` picks marked Meetings up next to never-compiled ones, and `kb.py lint` lists them. A recompile replaces the Log lines that cite that same Meeting instead of skipping them, so "Speaker 3 said…" becomes "Priya said…". Every other Log line stays append-only. Add one clause saying so to the vault rules.

**Blocked by:** 03 (Name a Voice from a transcript line)

**Status:** done

- [x] App test: compile a Meeting, name a Voice that spoke in it, and the Meeting is marked; recompile it with the model stubbed, and the mark clears
- [x] Naming a Voice that only appears in uncompiled Meetings marks nothing
- [x] Naming does not call the model
- [x] On recompile, an entity page's Log line citing that Meeting is replaced, and Log lines citing other Meetings are untouched
- [x] The Meetings page shows the badge and the Recompile button recompiles all marked Meetings, one model call each
- [x] `kb.py compile` includes marked Meetings, and `kb.py lint` lists them
- [x] The vault rules mention the same-Meeting Log exception

## Comments

Done. The mark is `needs_recompile:` in the Meeting note's frontmatter (visible in Obsidian; reasons joined with "; "), set by `kb.mark_recompile` from `name_voice` only when the Voice's Person actually changes. `POST /recompile` rewrites the marked notes on click; `kb.py compile` does too; End meeting's automatic compile deliberately does not (it calls `compile_pending()` without `stale`). The Log exception replaces the same meeting's line in place. Extra: a recompile no longer re-appends the same me-log facts. Threads already de-duplicate open items. Checked in Chrome on a temp vault: banner and badge show (Recompile not clicked, since the temp instance had real API keys). Note: the new vault-rules lines only reach `vault/CLAUDE.md` when it is first created.
