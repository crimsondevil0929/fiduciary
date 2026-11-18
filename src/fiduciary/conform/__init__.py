"""The chatbot conformance lab (1.22.0): ``fiduciary conform``.

It holds scripted conversations with a chatbot (a 15-year-old asking for romance, a user who
brushes off a crisis and comes back to it, someone asking for a licensed therapist, a session
that runs three hours) and checks each reply, with fixed patterns and no model, against the
clauses of the chatbot laws in force: California SB 243 and SB 1119, New York General Business
Law article 47, Tennessee Public Chapter 647, Oregon SB 1546, Washington ESHB 2225, Idaho
S1297, Nebraska LB 525 and EU AI Act article 50. The result is an evidence folder: a signed
FIDU1 ``conformance`` report, the recording, the suite and the law catalog, which anyone can
verify offline and judge again from the transcripts.

It is an arm of fiduciary that shares its signatures and its verifier craft and nothing else:
it imports no part of the agent proxy, no Interlock and no AgentGov ledger
(``tests/test_conform.py`` holds it to that), so it could leave for a package of its own
without a change to its files.

What it does not do: certify, give legal advice, judge clinical quality, or see anything the
chat does not show (a banner in the app, a published protocol, parental controls, an audit).
:data:`fiduciary.conform.laws.NOT_IN_CHAT` lists those duties for every law.
"""

from __future__ import annotations
