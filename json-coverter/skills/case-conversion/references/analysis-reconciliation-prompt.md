# Text-only Analysis reconciliation — analysis-reconciliation.v1

Read the frozen request at `$request_path` completely. Answer only its `issues`.
The request already contains the relevant accepted text, structured records,
JSON paths, source hashes, timeline and locked facts. Do not read video, images,
other Cases, source code, target.json or any unfrozen file. Do not start another
session, run the Converter, or modify any existing file.

Write exactly one UTF-8 JSON candidate to `$candidate_path`:

`{"request_sha256":"…","complete":true,"decisions":[{"issue_id":"…","decision":"keep_unknown","reason":"…","citations":[],"alternatives":[],"additions":[],"execution_hint":null}]}`

Cover every request issue exactly once. `decision` is `keep_baseline`,
`add_derived`, or `keep_unknown`. `add_derived` can only propose paths listed in
that issue's `allowed_additions`, each as `{"path":"…","value":…}`; never
overwrite a known value. When no addition is permitted, do not use
`add_derived`. A source quote is `{"ref":"…","quote":"…"}` and must be an
exact, nonempty substring of that source's string `value`, or of its compact
JSON text when `value` is structured. Explain the chosen
interpretation, other plausible interpretations, and why they were not chosen.
Each alternative is `{"interpretation":"…","reason":"…"}`. A `keep_baseline`
or `add_derived` decision needs at least one exact citation; `keep_unknown`
may have none when no textual claim can be supported.

An `execution_hint` is optional and never establishes a formal fact. The only
supported hint is `{"kind":"provisional_link","census_issue_ids":["…"],
"reason":"…"}` for the exact issue IDs in an unresolved-identity issue. Cite
the text that supports the hint. Do not claim a Census identity merge when
`identity_groups` is empty or the identity remains unresolved. A same-frame
refutation of an Actor excludes that Actor from a proposed link.

Compare subject, frame and time before interpreting a discrepancy. Different
lanes at different times may describe ordinary motion. Accepted structured
values and explicit frame witnesses are locked; a narrative summary does not
authorize changing them. Unknown speed, position, unseen motion and exact
crossing time remain unknown observations. If the frozen text is insufficient,
choose `keep_unknown`. Do not invent quotes, measurements, identities, roads,
actions or verification claims to make the Target easier to generate.

Only this candidate is yours to write. Python will validate it, assemble the
Analysis and decide any later execution projection.
