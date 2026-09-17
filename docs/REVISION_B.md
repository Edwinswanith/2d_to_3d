# Revision B implementation status

Revision B replaces the earlier pipeline. The local workflow now continues from evidence
review into **draft** construction, preview, STEP/STL export and versioned correction. The
user's later request explicitly permits useful draft output while unresolved findings remain.
Paired-data acceptance still blocks manufacturing release, rather than hiding draft artifacts.
This is not a claim of 98% reading accuracy or complete production readiness.

## Implemented and exercised

- Immutable source, 300/600 dpi PDF renders, rotation transforms, native text, source hashes.
  Raster images keep their native resolution; no additional source detail is claimed.
- Configured, separate context, visual inventory and text calls. Visual inventory never receives
  text-reader output. Detailed per-view crops get independent inventory calls and page mapping.
- Exact model IDs, prompts, schemas, immutable responses, bounded attempts, usage and timing.
  A new model call needs a new run. Offline replay verifies all recorded artifact hashes and the
  implementation/configuration/prompt/schema fingerprint. Replay returns the saved result;
  it does not yet resume partially completed provider work.
- Strict context blocking: no silent mm default, missing identity/envelope, unit conflicts and
  an implausible envelope route to review. The upload selector cannot silently override a title
  block. When the sheet contradicts itself (for example "DIMENSIONS IN INCHES" beside a printed
  Ø218.0 envelope, impossible for this family), an explicit unit selection is accepted as a
  recorded engineer unit correction: the context keeps `sheet_unit`, a `UNIT_CORRECTION`
  finding stays visible, and the text reader is compared against the sheet's stated unit.
  A plausible sheet still rejects a conflicting selection. The provisional 2000 mm family
  envelope bound is configurable and needs shop review.
- Pinned Document AI adapter with fixed overlapping tiles and normalized token mapping.
  Missing processor/credentials remains UNAVAILABLE; there is no disguised Gemini OCR fallback.
- Requirement union retaining unmatched independent observations, raw tolerance text, general
  notes, source boxes and reader uncertainties. Exact whole numeric tokens are required. Raw
  tolerance strings remain uninterpreted; agreement never approves their geometric meaning.
- Versioned contracts for inventory, requirements, associations, numeric provenance and assumptions.
  Restricted numeric evaluator accepts citations, registered assumption IDs, dimensionally
  compatible +/- and division by literal 2, cardinal angle offsets, explicit datum zero, and
  cardinal centreline angles with reasons.
- Coverage findings for missing associations, absent/unclear counts, different cross-view counts,
  missing readers and text-vs-inventory feature categories. Different cross-view visibility is a
  review finding, not proof that the physical feature count is wrong.
- Independent STEP measurements for coaxial axial bands, axial circular hole patterns and
  straight cylindrical passages into a coaxial cavity. Counts, placement, depth and material
  obstruction are checked. Unimplemented measurements return UNKNOWN.
- Analytic B-rep inspection for gland rings; all-planar/faceted rings cannot pass it. This is not
  an arbitrary-part tessellation detector or complete AP242 conformance test.
- Paired-data evaluation validates input hashes, a frozen candidate, approved positive references,
  family split isolation, real-only acceptance metrics and seeded structural errors. Synthetic
  tests cannot unlock the gate. Scan and clean metrics are reported separately.
- Browser inventory review with source highlighting, visible open findings and downloadable
  ledger/JSON/annotated report. Legacy bodies are explicitly marked unverified.
- Closed draft feature specification, dimensional provenance checks, required per-feature
  parameters, explicit coordinate policy and separate registered assumptions.
- Deterministic revolved polygon bodies (including grooves), axial hole patterns, tap-drill
  geometry, straight/oblique radial passages, cylindrical bore notches, open slots from a
  pitch circle through the outside diameter (`od_slot`) and circular chamfers.
  Failed/ineffective/splitting cuts name their feature and retain the previous valid solid.
- Printed thread forms resolve to one nominal drill through `thread_drill_mm`: unified
  (`#10-24`, `5/16-18 UNC`), metric (`M8`, `M8x1.25`) and pipe threads written as fractions or
  decimals (`.375 NPT`, `3/8-18 NPT`). Unknown sizes fail the named feature and ask for a
  cited drill; a port may cite its printed flat drill as `entry_diameter` instead of the table.
- Multiplier notation (`4X Ø.562`, `2X.132`, `2~ Ø14MM`, `10 HOLES`) yields an integer count
  child reading; a decimal before the word HOLE is the hole size, never a count. A number
  glued to letters is an identifier or thread code, never a reading: `GEBK472722A` no longer
  yields a 472-metre "length" that masked the axial-length fallback, and `M8x1.25` yields no
  pitch; `R.062`, `14MM` and `10X` still read. A reader that
  labels such a callout as a count no longer aborts the audit: the record is kept as a note.
- Through-hole measurement accepts a hole that exits into void where the flange is thinner than
  the body; a gap that still has host material around the corridor remains a short drill.
- Every printed length/diameter that neither the profile nor any feature cites becomes a
  visible `D` layer `UNKNOWN` check (`uncited_dimensions` in the status). The model proposal
  gets one bounded coverage retry naming exactly those readings; a rejected provider request
  (HTTP 4xx) is not retried and never appended to the prompt as a "schema error".
- The spec response schema is one flat `features` list; per-kind groups exceeded the
  provider's schema limit (HTTP 400). Kind-specific required fields are enforced locally.
- Radial ports enter at the body's local outside radius at their `z` (the hub on a stepped
  body), not the global flange OD. The restricted evaluator accepts a cardinal centreline
  constant (0/90/180/270) only as an angle offset (`180+R45_n1`) and lets an expression
  reference a registered assumption ID; a constant in a length expression is still rejected.
- Every proposal is validated before CAD: unknown citations, dimensionally wrong references,
  negative or duplicate profile vertices and a profile radius beyond half the printed envelope
  produce a targeted retry naming the offending field instead of a lost build attempt.
- Axial holes may start on a recessed host face: an explicit `z` names the exposed face the
  drill enters (a flange face behind the nose), verified by material just inside and void just
  outside at every hole; a buried plane is still rejected by name.
- Proposal search (`revb_proposal.request_proposal`) is bounded per cause: up to three rejected
  proposals each earn a targeted retry, transient provider faults (timeouts, 5xx) never spend one
  of those, and when every proposal was rejected the last one is salvaged — each invalid feature
  becomes a named `unresolved` entry (`salvaged.json`) so the printed count it leaves unbuilt
  fails by name instead of the whole draft being lost.
- One bounded geometry-correction round: the first build lands in `draft-1/`; when construction
  fails for named features, those exact receipts plus the previous proposal go back to the model
  once (`correction-prompt.txt`, `correction-response-*.json`), the revised proposal is built in
  `draft-2/`, and only a draft with fewer failures is promoted into the model folder
  (`drafts.json` records both). Engineer-corrected specs are built once and never re-asked.
- A threaded port whose printed tap drill bottoms at the full depth (`Ø18.24 BOTTOM DRILL 40
  DEEP`) is accepted with `entry_depth == depth`: the entry drill is the whole passage.
- A non-cardinal `centreline` angle in a proposal (`45` for a bolt pattern shown at 45°) is no
  longer a rejection: it becomes an auto-registered `IMPLIED_CENTRELINE` assumption
  (`A_centreline_n`, value and reason recorded, source marked as model-asserted) referenced
  by that field, so the pattern is built and the angle is reviewed under the AS layer.
  Rejecting it made the model omit the angle on retry and lose the whole pattern. A
  non-numeric centreline still fails by path.
- Inventory association type checks treat `tapped_hole` and `hole_pattern` as one visual
  class (a tap drill in a plan view is a hole; the thread is text). Other kind mismatches
  (a hole pattern claiming a port observation) still FAIL.
- A feature a salvaged proposal dropped is a `D` layer FAIL check naming the feature and the
  rejection, so the draft is PARTIAL — never a quiet `unresolved` line.
- Pre-CAD validation names self-intersecting profiles (a bore groove diameter used as an
  outside radius, or a wall retraced with zero thickness) edge by edge; the revolve accepts
  such polygons and every later boolean fails with an opaque OCCT error.
- Axial holes without a cited `z` enter the first exposed annular face met from their host
  side whose radial span holds the pitch radius, read from the cited profile and tested on
  the revolved body; when that is not the host plane an `H2` UNKNOWN check reports the resolved
  face. A pitch circle in void at every candidate fails by name. A hole starting in void at
  the host plane is no longer built silently.
- Multi-solid cutters (hole patterns, OD slots) are cut one solid at a time: on some bodies a
  single boolean with a fused cutter removes nothing or intersects as empty.
- A feature citing an inventory id that does not exist is rejected before CAD with the id
  named (a targeted retry), instead of building and then failing the association.
- A kind disagreement between the visual inventory and the specification (section pins read
  as a "port" versus an axial `hole_pattern`) is an `UNKNOWN` review item naming both readings
  and the view; two model readings are not ground truth for each other.
- A correction draft that deletes features is not an improvement: every feature the first
  draft proposed but the revision no longer builds counts as a failure of the revision
  (`dropped` in `drafts.json`), so a revision is promoted only when it fixes more than it
  drops. Live runs showed the model "fixing" a failed chamfer or pin pattern by omitting it.
- Chamfers snap to the nearest circular edge within 0.25 mm (a cited limit against a nominal
  used in the profile differs by microns); a farther miss fails naming the nearest edge's z
  and radius so the correction round can cite the right vertex.
- `counterbore` capability: an axial counterbore enlarging each hole of a pattern from its
  host face (diameter, depth, pcd, count, angle) or a flat spotface at a radial port entry
  (host `outside`, diameter, depth, z, angle, the port's tilt), cut from outside the curved
  surface so the footprint is trimmed flat. Spotfaces were the one printed feature class no
  builder kind could represent.
- Prompt rules taken from measured deviations: a tapped hole's depth is the tap-drill depth,
  never the tap depth; a hole printed THRU spans the whole body at its pitch circle; a count
  on a port note means that many separate port features; every printed chamfer is a chamfer
  feature or a slanted profile edge; spotfaces are never omitted silently.
- Axial extent is checked as well as diameter (`H2` `axial_length`). The context stage now
  reads the printed overall axial length (`overall_length_value`/`_unit`, beside the envelope);
  when present the body must equal it — too short (a missing step; a 34.06 mm body on a
  38.9 mm sheet) and too long (a mis-chained stack, 34.06 + 13.49) both FAIL and are fed to
  the one geometry-correction round as `profile`. Without it, a body longer than the largest
  printed linear dimension FAILs, a length equal to a printed one is PASS, anything else is
  UNKNOWN. A recorded unit correction overrides the unit label the context stage copied.
  This is deliberately post-build, not a proposal rejection: a body 0.5 mm over a drill depth
  is worth naming, not worth losing the whole draft to.
- Reimported STEP measurements, named cut receipts, analytical B-rep validation and exported
  STEP/STL/mesh. Surface fragments map back to feature identifiers for 3D highlighting.
- XZ/YZ section SVGs and topology signatures measured from reimported STEP. The comparison
  utility checks boundary types and ordered line-turn adjacency against reviewed annotations.
  Missing references remain UNKNOWN. This is not a complete semantic drawing-view matcher.
- Drafts remain downloadable with UNKNOWN/FAIL findings. STEP integrity never means drawing
  conformance. The original audit and each model attempt are immutable and separately hashed.
- Correction editor, expected-version rejection, retained prior drafts after failed rebuilds,
  authenticated individual review decisions bound to the exact manifest, and immutable logs.
- Local container definition with CadQuery/OCCT and locked Python/frontend dependencies.

The separate legacy `api/index.py` entrypoint preserves the existing Vercel/R2 body-preview
deployment through `legacy_web_api.py`. Its faceted exporter is not used by the Revision B
builder and cannot pass Revision B's analytic B-rep gate. R2 storage exists for that legacy
workflow; Revision B currently uses local artifact storage. The `cad` installation extra
provides CadQuery for a minimal container install, while development dependencies include it.

Alternate whole-page and crop feature observations are retained rather than silently merged.
They are not canonical physical feature counts. Engineer-labelled evaluation and association
review must resolve duplicate appearances. Inventory observations alone do not establish recall.

## Not yet implemented or accepted

The following remain incomplete or require external acceptance:

- Full per-view text reading, per-callout high-zoom rereads and spatial dispute reconciliation.
- Reliable automatic semantic association and host-face confirmation on an approved paired
  dataset. The live test still needed a source-based draft correction; model proposals alone
  did not reconstruct this drawing correctly. Corrections are logged, not automatically promoted
  into engineer-approved evaluation truth.
- Complete section topology/adjacency validation for curved boundaries, arbitrary drawing
  section planes, and reviewed source-section annotations. Current measurement has a declared
  narrower scope; absent annotations remain UNKNOWN.
- Reviewed tapered/helical thread geometry, arbitrary ports/slots, automatic deskew and complete
  interrupted-stage resumption. Supported threads currently use tap-drill cylinders only.
- PostgreSQL/object-storage deployment, shared-user authentication, production release
  transactions backed by accepted evaluation/CAM evidence, retention/region approval and ops.
- Production model/prompt acceptance, independent real Document AI validation and AP242/CAM
  import approval. The emitted STEP is an analytic B-rep; AP242 is not certified by this check.

The release endpoint intentionally returns BLOCKED until acceptance/CAM evidence is implemented
and approved. An UNKNOWN review sign-off cannot waive FAIL, a missing measurement obligation,
or the dataset gate. The six engineering questions for 2H-66033 remain review dependencies.

## Draft and review API

- `POST /api/drawings`: evidence audit followed by a draft attempt when context resolves.
- `POST /api/drawings/{id}/build`: `{expected_version}` for a fresh proposal, or add `spec`,
  `reviewer` and `reason` to build a correction. Names here identify draft authors, not approval.
- `GET /api/drawings/{id}` and `/versions`: status, checks and immutable model manifests.
- `GET /api/drawings/{id}/files/{kind}`: `step`, `stl`, `mesh`, `spec`, `checks`,
  `model-manifest`, `model-ledger`, `associations`, `sections`, `section-XZ`, `section-YZ`.
- `POST /api/drawings/{id}/reviews`: `{expected_version,subject,layer,decision,reason}`.
  Configure `D2S_REVIEWER_TOKEN_HASHES` as a JSON map from reviewer names to SHA-256 token hashes;
  send the credential as `Authorization: Bearer ...`. Never put credentials in model prompts.
- `POST /api/drawings/{id}/releases`: `{expected_version,manifest_sha256}` and reviewer credential.
  Returns explicit blockers; production acceptance is not bypassable through this endpoint.

The local API uses one CAD worker and a process lock. It is not a multi-worker production
transaction system. Draft corrections and reviews preserve prior evidence and stale sign-offs.

## Run

```sh
uv sync --locked
npm ci --prefix ui
npm run build --prefix ui
uv run uvicorn drawing2step.web_api:app --host 127.0.0.1 --port 8000
```

For optional Document AI, install `uv sync --locked --extra cloud`, configure Application Default
Credentials and set `DOCUMENT_AI_PROCESSOR_VERSION` to an exact processor version resource:
`projects/PROJECT/locations/us/processors/PROCESSOR/processorVersions/VERSION`.
The initial adapter supports `us` and `eu`. Aliases such as `latest`/`stable` are rejected.
A Gemini API key does not configure a Document AI processor or its service-account permissions.

Model role overrides are `D2S_CONTEXT_MODEL`, `D2S_TEXT_MODEL`, `D2S_INVENTORY_MODEL`,
`D2S_LAYOUT_MODEL`, `D2S_SPEC_MODEL`, and `D2S_DISPUTE_MODEL`. IDs are recorded; an unavailable model produces an
unavailable stage, never a silent substitution. Layout defaults to `gemini-3.5-flash-lite`,
context/text/inventory to `gemini-3.5-flash`, and the reserved dispute role to
`gemini-3.8-flash`. Dispute orchestration remains gated work.

```sh
uv run drawing2step audit-drawing DRAWING.pdf --output work/revb/new-run --rotate 90
uv run drawing2step audit-drawing DRAWING.pdf --output work/revb/new-run --rotate 90 --replay
uv run drawing2step check-structure BODY.step EXPECTATIONS.json
uv run drawing2step eval-revb examples/revb-evaluation-template.json
```

Exit code 2 means blocked/review, not a successful acceptance. Every fresh run needs a new
output directory. Numerical expectations for `check-structure` are in millimetres/degrees in
an explicitly declared input frame; the tool does not infer a drawing-to-model alignment.

Local container (requires Docker; image build is not validated on this host):

```sh
docker build -t drawing2step-revb .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env drawing2step-revb
```

Use persistent `/app/work` storage with suitable ownership if retaining container results.
The container exposes a local unauthenticated review prototype, not a shared production service.

## Dataset manifest

Start with the empty evaluation template. Each case requires `id`, `group`, `quality`
(`clean`/`scan`), `split` (`development`/`held_out`), `synthetic`, `approved_by` and
`approval_reason`. An approved engineer must resolve drawing/reference discrepancies first.
`drawing`, `reference_step`, `truth` and `audit` each contain `{path, sha256}` relative to the
manifest. Set `frozen_candidate` to the audit's `candidate_fingerprint` after freezing a
candidate. Do not tune against individual held-out failures.

The truth JSON contains `inventory.features` in the Inventory schema and `structure`, a list
of independent expectations accepted by `check-structure`. The reference STEP must pass those
expectations before seeded failures can contribute to catch rate. Each `seeded_errors` entry
contains `step: {path, sha256}` and `subject`, the ID of the violated truth expectation.
A real drawing may not be duplicated under another case ID; identical reference geometry
cannot cross family groups. Twenty development and ten held-out approved real cases are the
minimum inherited from the original plan. These declarations do not replace authorised
reviewer authentication for future production releases.

The supplied `body-draft (1).step` is a negative example, not a correct reference pair. Six
engineering questions in Revision B remain unresolved, including reference faces, chamfer edge,
port depths, slot placement/length, plan orientation and the tapped-hole view discrepancy.

## Verification

Run `uv run pytest --cov=drawing2step --cov-fail-under=80`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, and `npm run build --prefix ui`.
Recorded-response tests make no paid calls. Live paid tests are separate experiments.

Accuracy against real sheets is measured, not estimated, with `uv run python -m
drawing2step.cli accuracy work/runs/<drawing>-v*` (`--truth-dir`, default `work/truth`).
A truth file per drawing (`drawing_number`, `aliases` for mis-read title blocks, `unit`,
`body` envelope, `features` with their printed numbers, `printed_dimensions`) is an
engineer-reviewable transcription of the sheet; it never grants acceptance. Each run gets
`accuracy.json` and a scoreboard row: body envelope right or wrong; each printed feature
`captured`, `partial` (built with named deviations such as `pcd built 68.000 mm, printed
80.000 mm`), `missing`, or `unsupported` (no builder capability, e.g. spotfaces); through
patterns re-measured on the reimported STEP (`verified`/`wrong`); and every printed
dimension `cited`, `assumed` (a limit-pair nominal used through a registered assumption),
`uncited` (read but never used — a modelling gap) or `unread` (never read — a reading gap).
Runs that stopped before reading still score, with everything missing.
Required regressions already cover wrong axial structure, absent units, implausible inch
envelopes, forbidden expressions, short drills, missing group counts, missing/extra/misplaced
holes and faceted STEP. The builder attributes split/ineffective cuts to named operations. Additional tests cover
review authentication, stale versions, artifact tampering, draft downloads during review,
section topology, dimensional typing and mixed-unit child readings.

## Primary references

- [Google model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)
- [Document AI REST API](https://docs.cloud.google.com/document-ai/docs/reference/rest)
