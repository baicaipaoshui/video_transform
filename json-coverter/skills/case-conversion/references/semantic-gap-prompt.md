# Semantic initial layout review

Read only the frozen request at `$request_path` and the accepted analysis at
`$analysis_path`. Do not read images, video, other Case files, targets,
conversion results, or implementation code. Do not ask another Agent to inspect visual media.

For every actor in `eligible`, judge initial longitudinal separation from Ego
using only accepted natural-language information about that first instant.
Use exact quotations from `first_detail` or `identity_description`. Preserve
explicit ordering and lead relations independently of the distance category.
Words such as near, close, medium-distance, or far may support a category;
mere `ahead`, same-lane membership, or `direct_lead_of` does not establish one.
Later trends (`opening`, `closing`, `steady`) and later relative positions do not
establish the initial distance. Choose `unknown` when the accepted first-instant
text does not support a distance category. Never infer a gap from vehicle size,
image geometry, lane markings, or the execution policy's metre anchors.

For revision `conversion-semantic-gap.v3`, write one JSON object to
`$candidate_path` with exactly:

```json
{
  "revision": "conversion-semantic-gap.v3",
  "request_sha256": "copy exactly from request",
  "decisions": [
    {
      "actor_id": "exact eligible actor ID",
      "frame_id": "exact eligible first frame ID",
      "category": "very_close|close|medium|far|unknown",
      "evidence_quotes": ["exact substrings from first_detail or identity_description"],
      "relations": [
        {"type": "ahead_of|direct_lead_of", "actor_id": "eligible actor ID or ego"}
      ],
      "reason": "explain which first-instant words support the category and relations"
    }
  ]
}
```

For a non-`unknown` category, cite at least one source phrase that actually
expresses initial separation. For `unknown`, cite relevant position or relation
wording and explain the absent distance cue. Use `ahead_of` when the source
establishes longitudinal order. Use `direct_lead_of` only for `ego` and only
when accepted text establishes this actor as Ego's nearest lead in Ego's lane.
Do not turn a relation into a distance category.

For a legacy `conversion-semantic-gap.v1` request, preserve its historical
text-only response shape exactly: `actor_id`, `frame_id`, `category`, singular
`evidence_quote`, and `reason`; do not add v2 fields.
Historical v2 requests use `semantic-gap-image-prompt.md`.

Cover every eligible actor exactly once. Do not output metres, modify analysis,
call the Converter CLI, create a Case, or alter any source file. The four metre
values in the request are execution policy anchors, not video measurements.
