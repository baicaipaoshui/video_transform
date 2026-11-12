# Archived core motion interpretation

Read `$request_path` only. This is frozen text and frame metadata. Do not open
the video or images, call video understanding, or change the source Case.

Each `intermediate` entry separates identity-bound `observations` from
`contested_behavior`. The latter is a historical draft, not lane evidence.
For every intermediate frame, choose `source_lane_id`, `target_lane_id`, or
`null` from `allowed_lanes`. A non-null lane requires an exact quote from a
same-frame observation whose `lane_id` matches the choice. If there is no
such observation, choose `null` and quote a same-frame observation's text.
Never cite `contested_behavior` or a different frame to prove a lane. Preserve
the frame IDs and order. The source, target, and `fixed_source_observations`
are fixed by Python; do not claim an exact crossing time.

Write only `$output_path` as JSON:

`{"request_sha256":"…","decisions":[{"frame_id":"…","lane_id":null,"quote":"…"}]}`

Include one decision per intermediate frame. Python will evaluate each field
against the frozen source and retain the original candidate unchanged. An
unsupported lane choice is discarded while valid fields survive. Never invent
frame evidence or measurements.
