# Implementation plan: source contract, datum registry, and repair controller

Responds to `2D_to_STEP_Harness_Review_and_Plan.md` (external review, archive
`796f51621624ae0ccf11c54e096dd1d523580f69ccdedbf58ce044a6132af443`). This plan re-verifies
every finding against the current repository (not the reviewed ZIP — several things changed
this session) and turns the remaining gaps into ordered, file-level work.

**Decision, unchanged from the review:** extend Revision B. Do not restart with a generic
multi-agent framework. The harness (independent inventory, requirement ledger, typed feature
proposal, deterministic CadQuery builder, STEP reimport, structural checks, immutable
attempts, blocked release) is real infrastructure and stays. What's missing is an
independently accepted, spatially explicit **source contract** that a verifier checks the
candidate against — right now the candidate checks itself.

## 0. Finding-by-finding status against the current repo (2026-09-17)

Verified by reading the live source, not re-running the reviewer's probes.

| # | Finding | Status | Evidence |
|---|---|---|---|
| F1 | Two runtimes shipped (legacy Vercel path vs. Revision B Docker path) | **Done (2026-09-17)** | `api/index.py` now serves `web_api.create_app` (Revision B); `legacy_web_api.py` is unwired and its module docstring says so. Health and every job manifest (audit-manifest.json, model inputs.json) carry `pipeline_version`, `builder_version`, `commit`, `supported_geometry` (new `deployment.py`). Regression: `test_web.py::test_vercel_entrypoint_runs_revision_b_not_the_legacy_workspace` fails if this ever regresses. **New caveat surfaced by this change, not yet solved:** Revision B's job storage is local disk (`root`), with no cross-instance persistence layer; the legacy runtime specifically used R2 (`web_storage.py`) because a status poll can land on a different serverless instance than the one running the build thread. Vercel deployment of Revision B is correctness-risky under real multi-instance traffic until a durable storage layer exists — the Docker deployment (persistent disk) is the reliable target in the meantime. Noted in `api/index.py`'s docstring. |
| F2 | Harness infrastructure already substantial | Unchanged, agreed | No action beyond wiring it correctly (below). |
| F3 | Candidate self-checks, not source-checked | **Milestone B lands a genuinely independent verifier (2026-09-17), not yet wired to real drawings** | New `source_contract.py`/`datums.py`/`source_verification.py`: `verify_contract(step_path, contract)` re-imports the STEP fresh and measures it against a hand-authored, reviewed `SourceContract` — never against the candidate's own `Numeric` citations. `build_from_audit` runs it automatically whenever `source-contract.json` exists next to the audit (`revb_build_pipeline.py`), so this is wired into the *normal* build path, not a side script. Regression `test_an_accepted_source_contract_rejects_a_wrong_bore_through_the_normal_pipeline` reproduces the review's own probe end to end: an 80 mm bore built from a misread 80 mm ledger entry passes every existing (self-referential) check, and only the independent contract check FAILs it. Still open: no contract yet exists for the three real drawings this session tested (GEBK472722A, 2H-183624, 1H-141756, 2H-66033, 1H-139899) — Milestone B's schema is proven on a synthetic fixture, not yet turned loose on them; and `verify_contract` only covers `axial_band`/`hole_pattern`/`passage` shapes (reusing `check_structure`'s three existing handlers) — no shoulder-plane or "drill reaches this groove" connectivity handler yet, so F7-style connectivity claims remain unverified independently. |
| F4 | `datum` resolves to zero with no registry | **Done for independent verification (2026-09-17); done for the candidate path too, but only through profile_compiler.py, not yet through `Numeric` itself** | New `datums.py`: `DatumRegistry.resolve(name)` raises on any name it wasn't given evidence for, and only `face_a` is ever implicit (defined as the coordinate origin, not defaulted). `source_contract.py`'s `SourceContract` validates at load time that every requirement's `referenced_to` resolves, so a contract citing an unregistered datum fails to load rather than silently measuring from the wrong place — reproduces the plan's own worked example (2H-66033's port at 0.980 in from datum -A-, itself 0.125 in from face A, resolves to an absolute 1.105 in, not 0.980 in). New `profile_compiler.py`'s `AxialPosition` (`{datum, offset}`) reproduces the exact same worked example for the *candidate construction* path (`test_a_span_offset_from_a_registered_datum_is_not_the_bare_local_number`), by resolving through `DatumRegistry` directly rather than through `Numeric.datum`. Deliberately **still** unchanged: `revb.py:210`'s `evaluate_numeric` itself (used by every plain `Numeric`, i.e. every `Feature` field and every raw `DraftSpec.profile` point) still resolves any `Numeric.datum` to `Decimal(0)` — extending the shared evaluator so `Feature.z`/`depth`/etc. could ALSO cite a real second datum (not just profile spans) would mean changing code every other candidate-facing field depends on; that is a larger, riskier change than adding a new, opt-in module, and is not done here. |
| F5 | Threaded port too coarse for compound features | **Done for the general case (2026-09-17)** | New `Feature.kind == "compound_port"` (`revb_model.py`): a `segments: list[PortSegment]` field, each segment its own independent `z`/`angle`/`tilt`/`diameter`/`thread`/`termination`, generalizing beyond the review's own two-axis (`entry_axis`/`follow_on_axis`) sketch to N independently-angled stages fused into one cutter. Plain `port` is untouched and stays right whenever one shared axis is actually correct (unchanged: printed flat/tap drill as `entry_diameter` distinct from the follow-on `diameter`, bottomed-drill case). Regression `test_a_reviewed_compound_port_spec_cuts_two_independently_angled_segments` builds a two-segment port (15°/20° tilt, independently positioned and thread-sized) via `build_model` directly and confirms both segments' passages measure `PASS` through `check_structure`. **Not yet done:** `compound_port` is buildable but deliberately not proposable — `spec_schema()` strips `segments`/`compound_port` from the provider-facing schema (a full `PortSegment` `$ref` pushed the flat schema from ~3.9 KB past its ~5 KB provider budget, and `SPEC_PROMPT` doesn't document the kind yet), so extraction still can't produce one; and the acceptance test uses synthetic numbers, not 2H-66033's own (0.98 in/1.06 in/21°) — no source contract exists yet for that sheet to drive a same-numbers regression. |
| F6 | Repair loop too narrow | **Substantially reworked, one real gap and one live bug remain** | Already done: provider-fault vs. schema-rejection budgets are separate (`revb_proposal.request_proposal`); the correction round is bounded to one attempt (`_construct_with_correction`); promotion now requires the revision to fix more than it drops (`dropped` in `drafts.json`) — closing exactly the "delete the failing feature" loophole the review names. Still open: `build_from_audit` computes uncited-dimension and inventory-association checks *after* `_construct_with_correction` returns, so those D-layer failures never reach `construction_failures` and never trigger the one repair round. Live bug: `geometry_feedback` (`revb_proposal.py:641`) says "keep... the profile... exactly as before" even when the fed-back failure is the profile's own axial length (`construction_failures` inserts `("profile", ...)` — `revb_proposal.py`) — a direct self-contradiction in the prompt. |
| F7 | Sections aren't drawing-section validation | **Still open** | `revb_sections.py` compares ordered edge types/turning adjacency against a reviewed reference; still no metric dimension check, still fixed XZ/YZ planes. |
| F8 | No fine-crop tool for spec/repair stage | **Still open** | `build_from_audit` sends one whole `drawing.png` per spec/correction call; the per-view crops that exist for the inventory stage are not reachable from spec or repair. |
| F9 | Accuracy score is a diagnostic, not a conformance gate | **New module this session, same distinction the review asks for — but still coarse, not tolerance-aware** | `revb_accuracy.py` (built earlier today) already separates reading (`unread`/`uncited`/`cited`/`assumed`), feature capture, and body/placement checks — this is the "keep it a diagnostic" half of F9's recommendation done. Still missing: `TruthFeature`/`TruthBody` carry only nominals, no drawing tolerance limits; matching still uses a flat 2%/0.15 mm window (`RELATIVE_TOLERANCE`, `ABSOLUTE_TOLERANCE_MM`). The review's own example (19.35 mm against a 19.177 mm upper limit reading as "close") reproduces cleanly against the current tolerance constants. |

Net: the review's architecture diagnosis is correct and mostly still applies. F6's specific
repair-loop shape is the one place this session's independent work already anticipated the
review's fix; that validates the direction rather than replacing the need for the rest.

## 1. Target architecture (unchanged from the review, restated against real modules)

Three separate, hash-bound records, mapped onto files that exist or will exist:

```text
PDF evidence (existing: revb_pipeline.py inventory/reading)
  -> accepted SOURCE CONTRACT      (new: source_contract.py, datums.py)
  -> candidate SPECIFICATION       (existing: revb_model.DraftSpec, revb_proposal.py)
  -> deterministic construction    (existing: revb_model.build_model)
  -> export STEP, fresh reimport   (existing)
  -> OBSERVED MEASUREMENTS         (new: source_verification.py, extends revb_geometry.py)
  -> PASS / named discrepancy / unsupported-obligation
  -> targeted crop + scoped patch  (new: repair_controller.py, extends revb_build_pipeline.py)
  -> rebuild, full non-regression recheck
  -> final fresh import, full check, hash-bound delivery
```

The source contract is authored once per drawing (manual transcription first, exactly as the
review specifies — do not let extraction author its own grading criteria) and is immutable
during repair. The candidate specification is what `DraftSpec` already is. The observed
measurements record is new: it must be populated by loading the *exported* STEP fresh and
measuring it against the contract, never by echoing `Feature` field values (which is what
`resolved_features` in `revb_accuracy.py` does today, appropriately, for a diagnostic — a
conformance gate cannot use the same shortcut).

## 2. Milestones

Ordered so each proves something before the next spends effort on top of it, matching the
review's Milestones A-E, adjusted for what's already true of this repo.

### Milestone A — Confirm and expose the deployed runtime (P0, ~0.5 day)

The review's first finding is a deployment-identity gap, not a modelling gap, and it is cheap
to close before anything else because every later milestone assumes Revision B is what a
customer's job actually ran.

- Add to both `web_api.py`'s and `legacy_web_api.py`'s health/status responses:
  `pipeline_version`, `builder_version` (already exists as `BUILDER_VERSION`), a
  `supported_geometry` list (from `revb_model.CAPABILITIES`, already exists — just expose
  it), and the deployed git commit (read at build time into an env var or a generated
  `_version.py`, since the running container may not have `.git`).
- Add the same fields to every `manifest.json` a run already writes (`revb_build_pipeline.py`
  already writes `implementation_sha256`, a hash of the builder source files — add the human
  fields alongside it, not instead of it).
- Decide and document Vercel's fate: either point `api/index.py` at `web_api:app` (retiring
  the legacy faceted-body path for good) or keep both but make the health check and the
  upload UI show which one answered the request, so a feature-complete job can never
  silently land on the legacy exporter. **This needs a decision from you before I touch
  `vercel.json`** — retiring the legacy path is a behaviour change for whatever currently
  depends on it.
- Tests: `test_web_api.py`/`test_legacy_web_api.py` assert the new health fields; a new
  regression asserts the two apps' health `mode` values are distinct strings so a future
  refactor can't accidentally merge them silently.

### Milestone B — Source contract, datum registry, and an independent verifier (P0, ~4–6 days)

**Status: minimum version done (2026-09-17).** `datums.py`, `source_contract.py`,
`source_verification.py` exist and are wired into `build_from_audit` (whenever
`source-contract.json` sits next to `audit.json`, its requirements are re-measured against a
freshly reimported STEP and added to `checks.json` as `layer: "SRC"`, independent of anything
the candidate cited). The acceptance test named below — an 80 mm bore against an accepted 40 mm
requirement fails automatically, no manual script — passes
(`tests/test_revb_drawing_edges.py::test_an_accepted_source_contract_rejects_a_wrong_bore_through_the_normal_pipeline`).
The schema and prose below describe the milestone as first envisioned; the shipped version is
deliberately smaller — see the "What actually shipped vs. this sketch" note after it before
reading the code sketches as current.

This is the core of the review and the one milestone that must land before anything else in
this list is worth doing — a repair controller with nothing independent to check against just
repairs the model's own opinion faster.

**What actually shipped vs. this sketch, and what's still open:**
- `DatumRegistry` (in `datums.py`) is simpler than sketched below: one flat list of named
  positions in mm from `face_a`, no separate `plane`/`axis` kind, no `z_of(ledger, assumptions)`
  resolution against the candidate's own citations — a datum's position is a plain reviewed
  number, not itself computed from ledger readings. That was enough for the acceptance test and
  keeps the registry's own correctness independent of any ledger-parsing bug.
- `SourceContract`/`SourceRequirement` (in `source_contract.py`) use three concrete shapes —
  `axial_band`, `hole_pattern`, `passage` — mirroring `check_structure`'s three existing
  handlers exactly, rather than the free-form `ContractFeature`/`dimensions` dict sketched
  below. That covers bore/OD-over-a-span, axial hole patterns, and radial ports/drills; it does
  **not** yet cover a shoulder plane in isolation or a named `connectivity` claim like "must
  reach groove_lo" — `check_structure` has no handler for either, so `source_verification.py`
  can't ask for one yet. Extending both is still open.
- No contract exists yet for any of the three real sheets this session tested against
  (GEBK472722A, 2H-183624, 1H-141756) or the two added later (2H-66033, 1H-139899) — the
  acceptance test uses a synthetic fixture, per the review's own "initially, use manually
  reviewed drawing contracts to test the rest of the pipeline." Authoring one by hand for a real
  sheet, end to end, is the natural next step before trusting this on held-out drawings.
- The "mutation battery" below (remove a hole, move a shoulder, shift a port off datum,
  stop a drill short, wrong cavity, dimension just outside tolerance) is not written; only the
  bore-mismatch case from the review's own probe has a regression test so far.
- `evaluate_numeric` (the candidate's own construction path, `revb.py:210`) is untouched — see
  the F4 row above for why that is a deliberate, currently-low-risk scoping choice, not an
  oversight.

**New module `datums.py`:**

```python
class Datum(Contract):
    id: str  # "face_A", "projecting_front_face"
    kind: Literal["plane", "axis"]
    z_from_origin: Numeric | None  # None until resolved against the modelling origin
    reason: str


class DatumRegistry(Contract):
    origin: str  # which datum id is z=0 in the modelling frame
    datums: list[Datum]

    def z_of(self, datum_id: str, ledger, assumptions) -> Decimal: ...
```

`Numeric.datum` stops being a free string interpreted as zero (`revb.py:210` today). It
becomes a lookup into a `DatumRegistry` carried alongside the contract/spec, resolved through
`z_of`. `face_A`, `projecting_front_face`, and `rear_face` must be able to resolve to three
different z-values, each with its own citation — reproducing the review's exact repro
(`face_A`/`projecting_front_face`/`rear_face` all currently evaluate to zero) as a regression
test that must fail before this lands and pass after.

**New module `source_contract.py`:**

```python
class ContractDimension(Contract):
    id: str
    kind: Literal["diameter", "radius", "linear", "angle", "count"]
    nominal: Numeric
    tolerance_plus: Numeric | None
    tolerance_minus: Numeric | None
    datum_from: str | None  # datum id this is measured from, when axial


class ContractFeature(Contract):
    id: str
    kind: str  # same kind vocabulary as Feature
    dimensions: dict[str, ContractDimension]
    pattern_frame: str | None  # datum/angle this pattern's angles are measured from
    connectivity: str | None  # e.g. "must reach groove_lo" — free text, checked below


class SourceContract(Contract):
    drawing_number: str
    revision: str
    unit: Unit
    datums: DatumRegistry
    body: list[ContractDimension]  # every stepped OD/bore/shoulder, ordered
    features: list[ContractFeature]
    notes: list[str]  # non-geometric obligations, kept visible not modelled
    reviewed_by: str
    reviewed_at: str
```

This is deliberately a stricter, tolerance-aware sibling of the `DrawingTruth` schema already
built in `revb_accuracy.py` this session — not a redesign of it. `DrawingTruth` stays the
diagnostic scoreboard's input; `SourceContract` becomes the input to the actual conformance
gate. Converting one to the other later (once contracts exist for all three sheets) is a
mechanical follow-up, not new design.

**Extend `revb_geometry.py` → new `source_verification.py`:** wraps `check_structure`,
generalizing `_axial_band` and adding shoulder-plane, groove and connectivity handlers so a
`ContractFeature` with `connectivity: "must reach groove_lo"` is measured (does the drilled
passage's endpoint enter the named groove's swept volume?) rather than left `UNKNOWN`
forever. Every handler takes only the reimported STEP and the contract entry — never the
candidate spec — which is the property F3 is about.

```python
def verify_contract(step_path: Path, contract: SourceContract) -> list[Check]:
    """The only place a build is checked against something it didn't write itself."""
```

**Regression tests (write first, must fail today, must pass after):**
- The review's own probe: pass a 40 mm bore diameter as a radius (80 mm bore) into the
  synthetic fixture; assert `verify_contract` returns `FAIL` on the bore dimension, not
  `UNKNOWN`. (`check_structure`'s existing `_axial_band` already does this when *given* the
  right expectation — the new test is that a contract-driven call path produces that
  expectation automatically, without a human writing the JSON by hand as the CLI does today.)
- Mutation battery, one test per mutation, all against one small synthetic gland-ring fixture
  (reuse `tests/test_revb_drawing_edges.py`'s `ledger()`/`spec()` helpers): remove a hole,
  move a shoulder while preserving overall length, shift a port's z reading off datum A by a
  fixed offset, stop a drill short of its target groove, connect a drill to the wrong cavity,
  push one dimension just outside its contract tolerance. Each must produce a named `FAIL`;
  none may silently pass.

### Milestone C — Fix the schema gaps the review names, prove them with a hand-authored spec first (P1, ~3–4 days)

The review is explicit that a stronger model can't compensate for a representation with no
field for the geometry — prove the representation first, without any AI call.

**Status: compound_port, profile_compiler.py and the F6 prompt-contradiction fix all shipped
(2026-09-17); feeding D-layer obligations into the repair round is still open (that is Milestone
E / `repair_controller.py` territory now, not this milestone's remaining scope).**

- **`compound_port`** — shipped, as a new `Feature.kind` rather than an extension of `port`'s
  own fields. `PortSegment` (`revb_model.py`) carries its own `diameter`/`depth`/`z`/`angle`/
  `tilt`/`thread`/`termination` (`Literal["thru", "blind", "meets_cavity"]`)/`target`; a
  `compound_port` feature requires `host == "outside"` and `len(segments) >= 2` (a single axis
  stays a plain `port`). `build_model` computes each segment's entry point independently via
  the same `outer_radius_at` mechanism the single-port branch already used, cuts its own
  cylinder, and fuses every segment's cutter into one `Compound` — not a chained/derived single
  axis, and not limited to exactly two stages the way the review's `entry_axis`/`follow_on_axis`
  sketch was. No conical taper envelope was added (still out of scope, per the review's own
  caution against inventing NPT depths) and no `groove:<id>` connectivity termination exists yet
  — `termination` distinguishes `thru`/`blind`/`meets_cavity` (an endpoint-in-void heuristic,
  reusing the existing single-port H2 check) but can't yet name a specific target cavity by id;
  `PortSegment.target` is reserved for that and currently unused by any checker.
- **Test**: `test_a_reviewed_compound_port_spec_cuts_two_independently_angled_segments`
  (`tests/test_revb_drawing_edges.py`) hand-authors a `DraftSpec` (no Gemini call) with a
  two-segment compound port (15° and 20° tilt, independently z-positioned and thread-sized) and
  asserts it builds, both segments' passages measure `PASS` through `check_structure`, and the
  combined cutter leaves no residual material (`H1` check). It uses synthetic numbers tuned to
  this repo's existing gland-ring test fixtures, **not** the review's own 2H-66033 case (entry at
  0.98 in from A, follow-on at 1.06 in from A, 21° incline) — no `SourceContract` exists yet for
  that sheet to author a same-numbers regression against; doing so is the natural next step
  before trusting `compound_port` on that specific drawing.
- **Not yet done, still open:** `spec_schema()` deliberately excludes `compound_port`/`segments`
  from what's offered to the extraction provider (see the F5 row above) — `SPEC_PROMPT` doesn't
  document the kind, and its `PortSegment` `$ref` alone pushed the flat schema past the ~5 KB
  provider budget that already forced the flat (non-grouped) schema shape. Wiring extraction to
  propose `compound_port` needs either a slimmer per-segment encoding or dropping something else
  from the schema to make room, plus prompt text describing when to use it over a plain `port`.

- **`profile_compiler.py`** — shipped. `ProfileSpan` (external/bore, `straight`/`shoulder`/
  `groove`/`lead_in`) names each axial span; `compile_profile` resolves every span (diameter to
  radius, `AxialPosition`'s `{datum, offset}` through a real `DatumRegistry` — see the F4 row
  above), checks each surface's spans continue into the next with no gap or diameter mismatch,
  walks them into the exact boundary polygon `build_model` already consumes, and returns
  per-span `axial_band` checks plus the compiled overall length. That length is the external
  chain's own front-to-back extent — an identified endpoint difference, replacing "the largest
  printed linear dimension" entirely for any caller that adopts spans (the review's own
  complaint: the shortcut can confuse a transverse width with axial length, or reject a valid
  sum of adjacent spans; a compiled chain has no such ambiguity by construction). `build_model`
  gained two new optional parameters to support this without touching its existing behaviour:
  `points_override` (bypasses `spec.profile`/`Numeric` entirely — a compiled point has no
  citation left to re-verify) and `body_checks` (measured against the plain revolved body
  BEFORE any feature is cut; a `FAIL` there raises immediately, so a hole or port can never
  adapt to, and silently mask, a wrong host body). Tests:
  `tests/test_profile_compiler.py` (7 cases: compiled boundary and length, continuity/shape
  validation, the datum-offset worked example, and body-before-features).
  **Not yet done:** extraction still proposes a raw `ProfilePoint` polygon directly — nothing
  wires `SPEC_PROMPT`/the build pipeline to propose spans instead, so the old "largest linear
  dimension" fallback in `revb_proposal.axial_length_check` (`reliable_overall_length_mm`,
  strengthened earlier this session) is still what the *actual* running pipeline uses today;
  `profile_compiler.py` proves the replacement is buildable, not yet that it is used.
- **Done:** the F6 live prompt contradiction (`revb_proposal.geometry_feedback`) — it branches
  on whether `"profile"` is one of the failing ids and, when it is, tells the model to correct
  the profile's vertices instead of the previous unconditional "keep the profile exactly as
  before" (which directly contradicted asking it to fix a failure named as the profile's own).
  Regression: `tests/test_revb_proposal.py`.
- Still open, now Milestone E's `repair_controller.py` territory: feed uncited-dimension and
  inventory-association checks into `construction_failures` (or a sibling function) so they
  participate in the one repair round instead of only appearing after `build_from_audit`'s
  tail — closing the other half of F6.

### Milestone D — Attach extraction to the same verifier, add the crop tool (P1, ~3 days)

Only now does the AI call matter, and only because Milestone B gives it something to be
checked against.

- Run PDF → candidate spec (existing pipeline) against a `SourceContract` authored for the
  same sheet in Milestone B; report reading fidelity (`revb_accuracy.py`, unchanged),
  extraction-to-contract association, and `verify_contract` results as three separate rows,
  never blended into one score.
- **Crop tool**: expose the inventory stage's existing per-view crop mechanism to the spec and
  correction calls — a `request(prompt, crop=box)` variant of `Requester`
  (`revb_proposal.py`) that renders a bounded region of the same evidence image (with margin,
  keeping leaders/extension lines/neighbouring datums per the review's caution against
  isolating digits) and hashes the crop for the audit trail. Wire it into the one geometry
  correction round first — the repair prompt can now ask for a fresh crop around the specific
  failing dimension instead of re-reasoning over the whole page.

### Milestone E — Repair controller and fair model comparison (P1→P2, ~2–3 days + ongoing)

**Status: the core invariant-enforcing decision function shipped (2026-09-17); the true
multi-round budgeted loop and targeted inspection tools are still open.**

- **New `repair_controller.py`** — shipped: `evaluate_repair(previous_spec, previous_checks,
  candidate_spec, candidate_checks) -> RepairOutcome` replaces the old "did it drop more than it
  fixed" heuristic in `_construct_with_correction`. It rejects a candidate that (a) makes a
  required feature disappear, become `report_only`, or reappear with different citations under
  a new id (identity is tracked by `(kind, citations)`, not the id string, so a rename with the
  same citations is still recognized as the same obligation — not evasion); (b) turns any
  previously-passing `(layer, subject)` check into `FAIL`/`UNKNOWN`; or (c) is byte-identical to
  the previous spec (`spec_hash`) or fixes nothing new. `_construct_with_correction` now calls it
  directly (`revb_build_pipeline.py`), comparing `build_model`'s own checks plus a synthesized
  axial-length check (`_augmented_checks`, since that one is computed separately from
  `build_model`'s own output). Source-contract limits cannot loosen by construction elsewhere —
  a `SourceContract` is loaded fresh from its immutable file on every `verify_contract` call, and
  no repair round is ever given write access to it, so there is nothing for this module to
  enforce there. **Acceptance test passes**:
  `test_a_failed_required_port_demoted_to_report_only_is_rejected_not_promoted`
  (`tests/test_revb_drawing_edges.py`) reproduces the review's own named loophole end to end
  through the normal `build_from_audit` entrypoint — a port that fails to build, "fixed" by
  marking it `report_only`, is rejected and the loudly-failing original draft is kept instead.
  Unit coverage: `tests/test_repair_controller.py` (8 cases).
- **Not yet done:** this is still a single bounded correction round (as `_construct_with_correction`
  already was), not the true multi-round state machine with a configurable attempt budget the
  sketch above describes — `spec_hash`-based repeat detection is real and wired in, but nothing
  yet loops past one correction if it is rejected. Feeding D-layer obligations (uncited
  dimensions, inventory coverage) into that one round, so they can trigger a correction instead
  of only appearing in `build_from_audit`'s tail, is also still open (moved here from Milestone
  C's F6 note — the prompt-contradiction half of F6 is fixed, this half is not).
- Targeted inspection tools (contextual source crop, named drawing requirement, aligned CAD
  section, actual STEP measurement) for the repair round: not started.
- Model comparison harness (repeated runs per drawing per provider, `verify_contract`-driven):
  not started — needs live provider access and real held-out drawings, not a code change alone.

### PR4 — Exact-file final verification and the deliberately-wrong-part battery

**Status: shipped (2026-09-17), except the two items that need a live server/real drawings.**

- **New `final_verification.py`** — `freeze_release(step_path, checks, source_contract_path=)`
  hashes the exact STEP bytes, the exact checks list, and the source contract (when one exists)
  into one `ReleaseRecord`, and computes a `status` from four kept-separate values:
  `valid_solid` is never actually reachable as a *returned* status today (a solid that failed to
  round-trip is `blocked` instead, since nothing downstream of an invalid B-rep is worth
  measuring) — `nominal_drawing_conformance` (every check passed), `unresolved_requirements`
  (a valid solid, something UNKNOWN), or `blocked` (anything FAILed). `manufacturing_approval`
  is a separate, always-`False`-by-default field this module never computes — approval is a
  human decision, release status is a measurement, and conflating them was exactly the review's
  own caution. `verify_release` recomputes every hash from the bytes actually about to be
  delivered and raises if any of them disagree with the frozen record — never trusting the
  record's own claim. `build_from_audit` now writes `release.json` into every model folder
  (`revb_build_pipeline.py`), alongside the existing `checks.json`/`manifest.json`, with zero
  change to the existing `"release": "BLOCKED"` manifest semantics. **Acceptance test passes**:
  `test_verify_release_rejects_an_older_step_delivered_with_a_newer_report`
  (`tests/test_final_verification.py`) reproduces the review's own scenario directly — the
  same path rebuilt with different bytes, the older report rejected against the newer file.
  `test_build_from_audit_freezes_a_release_record_matching_the_delivered_step`
  (`tests/test_revb_drawing_edges.py`) proves the wiring through the real pipeline, not just
  the module in isolation.
- **Not yet done:** `/api/drawings/{job_id}/files/step` (`web_api.py`) does not call
  `verify_release` before serving the STEP — `release.json` is written but nothing reads it
  back at delivery time yet. This needs changes to a live-serving endpoint this session did not
  have a way to integration-test against a running server, so it was left as a clearly-scoped
  follow-up rather than an unverified change to the delivery path.
- **New `tests/test_deliberately_wrong_parts.py`** — 5 of the review's 6 named deliberate
  errors, each built as an internally-consistent (self-check-passing) candidate and caught only
  by `verify_contract` against a small hand-authored `SourceContract`, exactly matching the
  review's own framing: diameter used as radius (an 80 mm bore built where 40 mm was accepted);
  an internal groove modeled on the external surface instead (bore stays flat, OD wrongly
  stepped); a correct max diameter but a body 12 mm short of its accepted 30 mm length (caught
  by `_axial_band`'s own "expected axial interval extends beyond the model" — no new logic
  needed); an external locating boss replaced by an internal counterbore (OD stays flat, bore
  wrongly enlarged); and a hole drilled from the wrong face (built successfully, at the wrong
  end of the part, so the hole-pattern search window over its accepted z-span finds nothing).
  The sixth (required port demoted to `report_only`) was already covered by
  `repair_controller`'s own acceptance test in Milestone E, so it is not duplicated here. A
  sixth, correct-candidate test confirms all of the above pass together when actually built
  right — the verifier discriminates rather than blocking everything.
- **Not yet done, and not safely doable as a code change alone:** "test held-out drawings and
  repeated runs before claiming broader accuracy" — this needs live provider access and real
  drawings this session's synthetic fixtures cannot substitute for; the five real sheets already
  tested this session (GEBK472722A, 2H-183624, 1H-141756, 2H-66033, 1H-139899) have no
  hand-authored `SourceContract` yet, so `verify_contract`'s SRC-layer checks have never actually
  run against a real drawing, only against synthetic fixtures throughout PR1–PR4. Authoring one
  contract for a real sheet end to end remains the single most convincing next step before any
  claim of broader accuracy.

## 3. File-level backlog (supersedes the review's table with current paths)

| Priority | File | Change |
|---|---|---|
| P0 | `web_api.py`, `legacy_web_api.py`, `vercel.json` | Expose builder/pipeline/commit + supported-geometry in health and manifests; decide the legacy path's fate. |
| P0 | new `datums.py` | Datum registry; `Numeric.datum` resolves through it, not to a literal zero. |
| P0 | new `source_contract.py` | `ContractDimension`/`ContractFeature`/`SourceContract`, immutable, hash-bound. |
| P0 | new `source_verification.py` (wraps `revb_geometry.py`) | `verify_contract`; generalizes `_axial_band`; adds shoulder/groove/connectivity handlers. |
| P0 | new `tests/test_source_verification.py` | The 40→80 mm bore probe and the full mutation battery, all as automatic regressions. |
| P1 | `revb_model.py` | Done: `compound_port`/`PortSegment`, N independent segments fused into one cutter, explicit per-segment termination. Not offered to the extraction provider yet (schema budget; `SPEC_PROMPT` undocumented). |
| P1 | new `profile_compiler.py` | Done: `ProfileSpan`/`AxialPosition`/`compile_profile`. Not wired to extraction; `axial_length_check`'s "largest printed dimension" fallback is still what the running pipeline uses. |
| P1 | `revb_proposal.py` | Done: profile-repair prompt contradiction fixed. Still open: feed D-layer obligations into the repair round; crop-aware `Requester` variant. |
| P1 | `revb_sections.py` | Feature-aligned inspection planes; separate topology from metric conformance. |
| P1 | `revb_accuracy.py` | Add a `SourceContract`-driven conformance mode alongside the existing diagnostic scoreboard (kept, unchanged) — do not blend the two. |
| P1→P2 | new `repair_controller.py` | Done: `evaluate_repair` (no-evasion, no-regression, repeat-hash, no-progress). Still open: true multi-round attempt budget. |
| P1→P2 | new `final_verification.py` | Done: `freeze_release`/`verify_release`/`ReleaseRecord` (4-way status, hash-bound STEP+checks+contract). Wired into `build_from_audit` (writes `release.json`). Not yet wired into `web_api.py`'s own file-serving endpoint. |
| P1→P2 | new `tests/test_deliberately_wrong_parts.py` | Done: 5 of the review's 6 named deliberate errors (report_only rejection was already covered in `repair_controller`'s own test) plus one correct-candidate-passes case. |
| P2 | `web_api.py`, review UI | Show integrity / source conformance / unresolved obligations / review state as separate fields, bound to the delivered STEP hash. `/api/drawings/{job_id}/files/step` does not yet call `verify_release` before serving. |

## 4. Acceptance measures (unchanged from the review — restated as what to log)

Per run, record: drawing-obligation coverage (measured+adjudicated mandatory requirements /
all mandatory requirements), complete-part nominal conformance (every in-scope requirement
passing on the *same final* STEP), false acceptance rate (mutated candidates the verifier
wrongly calls conformant — this is exactly what the Milestone B mutation battery measures),
mandatory-unknown rate, and first-pass vs. post-repair conformance with cost/latency. None of
this is a substitute for holding out real drawings the contract-authoring process hasn't seen.

## 5. What I need from you before Milestone A/B can really start

- **The legacy-vs-Revision-B decision** (Milestone A): retire `api/index.py`'s legacy path,
  or keep both and surface which one served a given job? This changes `vercel.json` and is a
  behaviour change I won't make unprompted.
- **2H-66033 materials**: do you have the original drawing, the prior ChatGPT-produced STEP,
  and the `2H-66033_Dimension_Comparison.md` the review references? Milestone B's first
  source contract should be authored against that exact sheet, and Milestone C's hand-authored
  `compound_port` test is most convincing reproducing its exact numbers. Without it I'll use
  one of the three drawings already in this repo's `work/inputs/` and `work/truth/`, which
  covers the mutation battery fine but won't validate against the specific customer case the
  review investigated.
- **Provider access for Milestone E's comparison** beyond Gemini (the review mentions OpenAI)
  — only relevant once B–D exist; no action needed now.

## 6. Suggested execution order

A → B → (C and D's crop tool in parallel, both depend only on B) → D's extraction wiring →
E. Milestone B is the one that must not be skipped or shortened: everything downstream is
"repair against what B can now check," and the review's central point is that without B, more
repair budget or a different model just converges faster on an unverified answer.
