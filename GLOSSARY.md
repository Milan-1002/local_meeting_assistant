# Meeting Copilot

A local assistant that listens to Milan's meetings, transcribes them, and drafts answers in his voice from a wiki built out of past meetings.

## Meetings and their text

**Meeting**:
One recording run, from start (or New meeting) to End meeting, with one id. Restarts that cut one real call into several runs give several Meetings.
_Avoid_: Session, call, recording

**Transcript**:
The timestamped, line-by-line text of a Meeting, kept as heard and never edited.
_Avoid_: Raw meeting, log

**Live notes**:
The running summary of a Meeting, refreshed while it is still going.
_Avoid_: Summary, notes (on its own)

**Meeting note**:
The wiki page written for a Meeting after it ends, distilling what was decided and learned.
_Avoid_: Compiled note, summary, notes (on its own)

**Wiki page**:
One Markdown page in the knowledge base: a Person, project or topic page, a Meeting note, a weekly review, or a page the app maintains (voices, threads, glossary). Transcripts are not wiki pages.
_Avoid_: Note (on its own), doc, file

**Brief**:
A short preparation note written before or at the start of a Meeting from past Meetings with the same people and topics.
_Avoid_: Prep, summary

## Who is speaking

**Voice**:
A remembered voiceprint for one speaker, recognised again in later Meetings whether or not it has been named. Belongs to at most one Person; an unnamed Voice not heard for 30 days is forgotten.
_Avoid_: Voice ID, embedding, speaker profile

**Speaker N**:
The label of a Voice that hasn't been named yet, such as "Speaker 3". The number stays the same across Meetings.
_Avoid_: Unknown, Other, guest

**Person**:
Someone who appears in Meetings, with their own wiki page. A Person can have several Voices (the same colleague in a room and over Teams); naming a Voice links it to a Person.
_Avoid_: Contact, participant, attendee

**Naming**:
Linking a Voice to a Person. It applies to every Meeting that Voice appears in, past and future.
_Avoid_: Renaming, relabeling, tagging

**Me**:
The Person who is Milan. Lines from Me's Voice are never answered.
_Avoid_: User, owner
