# Optional archived-text draft preparation — description-fallback.v1

Read `$request_path` and its bound bundle_path. These are archived text, not a
request to understand the video again. Do not read images, video, other Cases,
source code or Target files. Do not delegate or start another session.

Write only `$output_path`, once, with exactly:
`{"request_sha256":"…","additions":[{"path":"/…","value":…, "source":"/…","quote":"…"}]}`.

Copy request_sha256 from the request. `path` is an existing parent object's
missing field in request.base. `source` is a JSON pointer into the frozen bundle;
quote must be an exact nonempty substring of that source string. Fill only
representation holes that can be explained by that archived text. Preserve
every existing value, Actor, trajectory, event, road and limitation. Never add
measurements, frame evidence, identities claimed as confirmed, or verification
flags. Do not overwrite fields, merge registered vehicles, alter facts or
invent missing motion. Numeric time values must appear literally in the quote.

This is an unverified interpretation. Python will validate additions against
the original Analysis schema and confirmed facts. Inadequate evidence must
remain inadequate: use an empty additions array when it cannot be repaired by
source-cited missing fields. Do not output a complete Behavior or Target. There
is only one optional preparation task; original relations and numerical tasks
follow only if the resulting Analysis is valid.
