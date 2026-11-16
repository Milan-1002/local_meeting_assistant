# Transcripts store Voices, not names

Transcripts are never edited, but Milan names speakers after the fact, often days later and across many Meetings at once. So a Transcript line records which Voice spoke, and a separate Voice → Person mapping supplies the name wherever the text is shown or sent to a model. Naming a Voice updates that one mapping and so applies to every past and future Meeting the Voice appears in. Meetings whose Meeting note was written before the naming are marked as needing a recompile instead of being recompiled automatically, so no LLM call happens without a click.

## Considered Options

- **Rewrite the Transcript on naming.** Simpler to read in Obsidian, but it breaks the never-edited rule, and naming one Voice would mean rewriting every Transcript it appears in.
- **Names only, forgotten per Meeting.** Rejected because the same 9–10 people attend most Meetings, so they would have to be renamed every day.
