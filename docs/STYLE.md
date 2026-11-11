# How the console, the demo and the docs are written

Everything a person reads here (the console, scenario titles and ledes, check descriptions,
error messages, READMEs, changelogs) is written the way a careful engineer explains
something to a colleague: in whole sentences, with the mechanism and the number.

`tests/test_prose.py` enforces the rules marked **(gate)**. It reads every string the console
and the demo can show and fails with the string, its file and line, and the rule.

## The rules

1. **Say what happened, to what, and how many.** "A legacy trigger reads `discount_pct` as
   the share the customer pays, so the order is repriced to 10% of its total" explains.
   "A trigger amplifies the discount into a revenue cut" does not.
2. **Whole sentences, with their articles.** No headline style ("Outbox relay crash recovery
   guarantees exactly-once execution"), and no fragments that need the previous sentence to
   parse ("Its own records open").
3. **One name for one thing.** The component in front of the stack is the *gateway* (or
   *input gateway* where it is being contrasted with the stack). Never "input proxy". A
   lineage is *revoked*, *suspended* or *expired*; an agent is *paused*. They are different
   events and the checks treat them differently. **(gate: gateway terms)**
4. **Never claim more than was measured.** Say "the customer is refunded once", and why
   (one idempotency key), not "guarantees exactly-once execution". Signed records make
   tampering visible; they do not prevent it. A stated limitation is never dropped to sound
   stronger. **(gate: "guarantee", "immediately detectable", "impossible to")**
5. **Use the system's own nouns.** A directory has files and path classes, a cluster has
   objects, an API has records. "Database rows" belongs to the SQL workflows only.
6. **No dash between two words. (gate)** An em dash set tight ("aborts—leaving the edit
   intact") is the clearest sign of machine text. Use a comma, a colon, or two sentences. A
   lone dash still stands for an empty cell.
7. **Counts take the form their number takes. (gate)** "1 file", "2 files". Never
   "file(s)".
8. **"Names" is not the verb for everything. (gate, budget of 3)** A bundle *contains*, a
   receipt *lists*, a check *reports*, a message *says*, a record *identifies*. "Named
   principals" is fine.
9. **A scenario title fits two lines of the rail. (gate: 52 characters)** It says what
   happens, in the scenario's own terms: "One escalation, and the desk opens an incident".
   The detail goes in the lede.
10. **A lede is two to six sentences. (gate)** No sentence over 38 words, 520 characters in
    all, ending with a full stop.
11. **No Markdown in a string the console draws. (gate)** The console draws text. A model's
    own emphasis in a recorded reply is rendered, not shown as asterisks.

## Words the gate refuses

| Instead of | Write |
|---|---|
| out-of-band, unmediated | what was done, by whom, without what: "sent by hand, outside the relay" |
| unattested, phantom event | "an event no receipt explains" |
| post-hoc, post-write | "afterwards", "after the write" |
| cryptographically, cryptographic linkage | what is signed, and with which key |
| guarantee, guarantees | what happened in the run, and the mechanism |
| seamless, robust, leverage, utilize, holistic | cut it; say the fact |
| zero-knowledge, tamper-proof, immutable document | the property that actually holds: "anyone can check it offline", "a change is detected" |

"Phantom" stays where it is AgentGov's own term for a reconciliation category.

## Changelogs and commit messages

They describe behavior: what was wrong, what changed, what a user must do. They never
describe the writing process ("editorial pass", "polished the prose").

## Adding to the gate

A word earns a place in `BANNED` when it has shown up in review twice. Add it with the
plain way to say it in the table above, and add a line to
`test_the_gate_catches_what_it_is_for`.
