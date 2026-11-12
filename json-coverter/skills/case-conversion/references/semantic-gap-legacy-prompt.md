# Historical semantic initial gap review (v1)

Read only the frozen request at `$request_path` and accepted analysis at
`$analysis_path`. Do not inspect video, images, other Cases, targets, conversion
results, or implementation code.

For every actor in `eligible`, select an initial distance category using the
first-frame `first_detail` text. Later motion trends alone do not establish
the initial distance. If the text has no distance cue, choose `unknown`.

Write exactly one JSON object to `$candidate_path`:

```json
{
  "revision": "conversion-semantic-gap.v1",
  "request_sha256": "copy exactly from request",
  "decisions": [
    {
      "actor_id": "exact eligible actor ID",
      "frame_id": "exact eligible first frame ID",
      "category": "very_close|close|medium|far|unknown",
      "evidence_quote": "exact substring of first_detail, or empty for unknown",
      "reason": "brief explanation"
    }
  ]
}
```

Cover each eligible actor exactly once. Do not output metres, modify analysis,
call the Converter CLI, create a Case, or alter source files.
