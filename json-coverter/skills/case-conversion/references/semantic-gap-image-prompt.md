# Semantic initial layout review

Read only the frozen request at `$request_path`, the accepted analysis at
`$analysis_path`, and image paths explicitly listed in `request.frames`. Inspect
every bound first-frame image before deciding. Do not read the video, other
frames, prior Cases, targets, conversion results, or implementation code.

Choose the initial longitudinal separation from Ego for every actor in
`eligible`. Use the first-frame image together with text describing that same
instant. Preserve explicit natural-language relations such as `far ahead`,
`ahead of actor_001`, and `initial direct lead of ego`. Later trends (`opening`,
`closing`, `steady`) do not establish initial distance. A vehicle may be in an
adjacent lane. Choose `unknown` only when neither the bound image nor the text
supports a distance category.

For revision `conversion-semantic-gap.v2`, write one JSON object to
`$candidate_path` with exactly:

```json
{
  "revision": "conversion-semantic-gap.v2",
  "request_sha256": "copy exactly from request",
  "decisions": [
    {
      "actor_id": "exact eligible actor ID",
      "frame_id": "exact eligible first frame ID",
      "category": "very_close|close|medium|far|unknown",
      "evidence_quotes": ["zero or more exact substrings from first_detail or identity_description"],
      "visual_evidence": "short first-frame observation, or empty only when unused",
      "relations": [
        {"type": "ahead_of|direct_lead_of", "actor_id": "eligible actor ID or ego"}
      ],
      "reason": "explain the joint text/image choice and relationships"
    }
  ]
}
```

Use `ahead_of` when the source establishes this actor is longitudinally ahead
of another actor. Use `direct_lead_of` only with `actor_id: "ego"`, only when
this actor is stated or visibly established as Ego's nearest lead in Ego's
lane. Do not infer same-lane membership from image position when the accepted
analysis binds another lane.

For a legacy `conversion-semantic-gap.v1` request, preserve its historical
text-only response shape exactly: `actor_id`, `frame_id`, `category`, singular
`evidence_quote`, and `reason`; do not add v2 fields.

Cover every eligible actor exactly once. Do not output metres, modify analysis,
call the Converter CLI, create a Case, or alter any source file. The four metre
values in the request are execution policy anchors, not video measurements.
