# Case Conversion Protocol

New v3 Cases may send one `analysis-reconciliation.v1` text request before
Analysis is committed. The worker reads only the compact frozen issue packet
and writes its candidate, following `analysis-reconciliation-prompt.md`.
Python validates exact quotes and allowed absent-field additions. An optional
provisional identity link is execution-only and never a Census merge. The
ordinary temporal motion stages and deterministic Converter remain unchanged.

## Frozen temporal motion decision for new Cases

New v3 Cases freeze `conversion-semantic-motion.v1`. Text-only model work
covers Ego and retained same-direction moving vehicles, including background
and late first sightings. It interprets timed prose and limitations, quotes the
bound evidence, and supplies initial positions plus piecewise linear speed knots
as **execution approximations**, never measured facts. Known numeric samples,
initial conditions, Actor IDs and evidence-derived lane changes are immutable.
Unresolved execution-relevant identity limitations stop before model planning
and return `identity_evidence_insufficient` to upstream review.

The existing `conversion_semantic_gap` task, state artifact names, receipt key
`semantic_gap`, and `--semantic-gap` CLI argument carry this versioned envelope.
New Cases also freeze `conversion_continuation=conversion-continuation.v1`:
two planned sessions (`conversion_relations`, then `conversion_semantic_gap`),
with cumulative `save_NNNN.json` checkpoints inside each session. Both stages
share at most one extra session, charged to the existing Case recovery budget.
The worker follows `conversion-continuation-prompt.md`; only the owner accepts
checkpoints and commits the final decision. Partial drafts never authorize
target generation. Without the continuation marker, the historical motion
contract still uses one planned session. Do not migrate historical Cases. Gap v1/v2/v3 keep
their original eligibility, prompts, distance anchors and failure behavior.

The new request binds analysis bytes, full eligible timelines, every supplied
text item, event evidence, initial conditions and the versioned motion policy.
A candidate must cover all actors and text items, cite exact source substrings,
and satisfy all timed relations by integration. No images or delegated vision.
Numeric unknowns are filled only on a private copy with per-field receipts;
synthetic time-zero and duration samples have no frame IDs. Converter emits
all speed phases as absolute simulation-time transitions. The existing lane
changer requires constant longitudinal speed; other actors may accelerate.

Target checks decode actual actions, verify the entire accepted speed profile,
and check ahead/alongside intervals at segment boundaries and relative-motion
extrema, plus approach/overtake windows. The existing continuous lane-change
collision gate still applies. Vehicle lengths and tolerances are frozen execution
policy assumptions. Source-contract prediction does not prove a real render.
Model failure, unresolved motion, bad citations, unsupported acceleration or
unsatisfied relations block delivery; equal-speed fallback cannot replace them.

## Frozen policy for new Cases (2026-09-26)

The public analysis schema is unchanged. New v3 Cases supply `--policy <private-json>` and `--policy-sha256 <digest>`. Verify the policy revision, content digest, analysis byte hash, scene and road source before projection. The receipt embeds the policy and digest; independent replay and cache reuse include this binding. A standalone call without a policy remains the explicit legacy compatibility path; Delivery never omits the policy for a marked new Case.

Defaults come from the frozen Case policy; each fallback's structured field path maps to `allowed_degradations` IDs. Standard new v3 requests automatically include the registered completion categories when the caller omits the allowlist, so unknown speeds and other execution values do not require manual input or per-field approval. Explicit restrictive lists remain effective. Saved Cases retain their lists unless the orchestrator's formal recovery adopts default completion; its signed, input-bound record is embedded in the new policy while model inputs and the original policy archive remain unchanged. Unauthorized or unclassified fields stop before target replacement with `conversion_policy_blocked`. Scene-local defaults cannot bypass this gate: new policies require projection to supply them first, otherwise `conversion.scene_default_unbound` stops. Changing known signal timelines is not a missing-value default. Known geometric conflicts remain hard failures.

The v2 highway mapping rejects missing required motion values, including following distance; the v3 fallback contract is not implicitly applied to v2.

## Deterministic fallback boundary

使用 `--workflow-contract video-case-world-behavior` 时，preflight 拒绝不可解析、缺少根对象以及弯道改直道。变化灯色按用户授权在执行副本中替换为全程绿灯，记录原始时间线和降级，不因此停止。其余执行缺口由 Converter 在私有副本中按“可用 analysis 值优先，否则硬编码兜底”补齐 road/lane、长度、宽度、停车映射、车型/颜色、初始状态和设施位置。观察 analysis 不被修改；逐字段记录写入转换回执。规则由 [fallbacks.md](fallbacks.md) 维护。

空 lane_connections 同样属于固定补齐范围，不要求关系复核证书，不据此拒绝生成 target。按道路方向与车道列表补出的连接仅为执行默认值；已有连接保持优先。显式复核投影是另一路受既有冻结策略约束的执行方式。

## Request

One request contains one `case_id` plus absolute paths for the validated `analysis.json`, target JSON and `conversion_result.json`, and `scene_type=highway|intersection`.

The deterministic CLI worker may read only the analysis input and converter-owned mapping/validation code. It must not read the video, images, batch files or actor registry. The new `conversion-semantic-motion.v1` model task reads only the frozen request and analysis; it must not inspect images or ask another Agent to do so. Historical v2 image bindings remain valid only for Cases frozen under v2.

## Conversion gate

1. Validate the analysis schema and ensure its scene type matches the request.
2. Build a private execution copy with Converter-owned deterministic fallbacks.
3. Select the matching mapping implementation under `scripts/scenes/<scene-type>/convert.py`.
4. Generate a temporary target and an explicit scene fallback array (including `[]`). Reject missing, malformed or structurally invalid scene records.
5. Directly load the authoritative models from `scripts/mela-business-logic/mela_business_logic/scenario_preview/pydantic_classes/` and validate all four Mela sections. Also validate IDs/references and the statically known `generate(data)` road-network constraints. Do not describe these checks as a real `mela-openx-generator` or esmini execution.
6. Before atomic target replacement, validate the complete staged receipt and target with the same gate used by final delivery. Replay only the existing scene script in a temporary directory; this is JSON generation, not video inference or rendering. Retain the independent semantic gates.
7. Atomically replace the target and commit `conversion_result.json` with status, scene type, input hash, target hash, output path, staged records, `excluded_actor_ids`, checks and error code.

Current v3 receipts use `fallback_receipt_version=staged-fallbacks.v1`: `execution_fallbacks` records analysis → private execution projection; `scene_fallbacks` records private projection → target generation; `fallbacks` must equal the ordered concatenation of the two. Both lists are present even when empty. Recompute execution records from analysis, replay scene generation to verify scene records and the final target, then check aggregate equality and actor exclusions. All applied fallbacks mark current v3 conversion `done_degraded`; policy execution remains distinct from source-verified motion.

Historical unversioned receipts retain their single aggregate list. When revalidated through the current fallback gate, compare it to the full two-stage replay; do not guess stage membership from reason strings or rewrite persisted Case receipts. New staged receipts missing either stage, or claiming an unsupported version, fail validation.

An existing result may be reused only when input hash, target hash, converter fingerprint and request contract match and authoritative Mela validation still passes. Current staged v3 cached results also pass the same complete receipt/target validation; historical cache receipts are not silently treated as staged receipts.

## Determinism and semantic coverage

Scene scripts initialize UUID5 generation from canonical analysis content; the fixed traversal sequence makes IDs repeatable for the same input and converter version. JSON uses sorted keys, UTF-8, two-space indentation, a final newline and finite numeric values. Validation uses two fresh processes with separate outputs/receipts, not cache reuse. The conversion receipt owns runtime paths and hashes; they are not injected into target.

The v3 wrapper also runs cross-section ID/name reference resolution and the statically known OpenX input checks before committing target. When fallbacks are present, source-motion equality is intentionally not asserted: the receipt marks it as policy-default rather than source-verified. Missing facilities remain absent; present facilities with missing execution positions receive the documented canonical placement. These checks do not execute OpenX or esmini.

## Boundary

The global orchestrator owns Case progress and failure state. This worker returns `conversion_failed` or concrete validation details, then stops. It does not generate reports, publish artifacts, start another Agent or answer the end user.
