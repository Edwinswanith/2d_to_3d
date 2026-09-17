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
| F3 | Candidate self-checks, not source-checked | **Still open, but the mechanism is proven** | `check_structure`/`_axial_band` (`revb_geometry.py`) run automatically at `revb_model.py:823`, but `expectations` are appended from the same `value(...)` calls used to cut the feature (`revb_model.py:487-497` etc.) — self-referential. This session added one genuinely independent check: `axial_length_check` compares the built body against the context stage's own separate reading of the printed overall length (`revb_proposal.py`), and it already catches a wrong-length body the builder's own checks pass. That is the F3 pattern working exactly once; it needs to generalize to every dimension, not just overall length. |
| F4 | `datum` resolves to zero with no registry | **Confirmed still open** | `Numeric.datum: str | None` (`revb.py:135`) is a bare label; nothing stores a plane/axis per datum name. `revb_proposal.py`'s prompt still just asks the model to declare "face A at z=0" as a string convention. |
| F5 | Threaded port too coarse for compound features | **Partially improved, still open for the hard case** | This session added: printed flat/tap drill as `entry_diameter` distinct from the follow-on `diameter`, and a bottomed-drill case (`entry_depth == depth`). But entry and follow-on still share one `theta`/`phi` (`revb_model.py:535-578`) — there is still no way to place the flushing axis at one z/tilt and the follow-on drill at a different z/tilt, which is exactly the 2H-66033 case (0.98 in vs. 1.06 in, 21° incline). |
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

This is the core of the review and the one milestone that must land before anything else in
this list is worth doing — a repair controller with nothing independent to check against just
repairs the model's own opinion faster.

**New module `datums.py`:**

```python
class Datum(Contract):
    id: str                          # "face_A", "projecting_front_face"
    kind: Literal["plane", "axis"]
    z_from_origin: Numeric | None     # None until resolved against the modelling origin
    reason: str

class DatumRegistry(Contract):
    origin: str                       # which datum id is z=0 in the modelling frame
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
    datum_from: str | None            # datum id this is measured from, when axial

class ContractFeature(Contract):
    id: str
    kind: str                          # same kind vocabulary as Feature
    dimensions: dict[str, ContractDimension]
    pattern_frame: str | None          # datum/angle this pattern's angles are measured from
    connectivity: str | None           # e.g. "must reach groove_lo" — free text, checked below

class SourceContract(Contract):
    drawing_number: str
    revision: str
    unit: Unit
    datums: DatumRegistry
    body: list[ContractDimension]      # every stepped OD/bore/shoulder, ordered
    features: list[ContractFeature]
    notes: list[str]                   # non-geometric obligations, kept visible not modelled
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

- **`compound_port`** (extends `revb_model.Feature`, or a new `kind`): independent
  `entry_axis` and `follow_on_axis`, each its own `{z, angle, tilt}` triple (reusing
  `Numeric`), separate `entry_depth`/`drill_depth`/`thread_depth` instead of the current
  single `entry_depth`, and an explicit `termination: Literal["blind","through","groove:<id>"]`
  checked by `source_verification.py`'s connectivity handler. Optional conical envelope width
  for a modelled taper, off by default (current tap-drill-only policy stays the default;
  taper is opt-in per the review's caution against inventing NPT depths).
- **Test**: hand-author a `DraftSpec` (no Gemini call, exactly the review's Milestone C) with
  a `compound_port` matching the review's own 2H-66033 numbers (entry at 0.98 in from A,
  follow-on at 1.06 in from A, 21° incline) and assert it builds, the two segments connect
  without an unintended breakthrough, and `check_structure` measures both axes independently.
- Fix the F6 live prompt contradiction now that it's cheap: `geometry_feedback` must not say
  "keep the profile exactly as before" when one of the fed-back failures is the profile's own
  axial length; branch the instruction on whether `"profile"` is in the failing ids.
- Feed uncited-dimension and inventory-association checks into `construction_failures` (or a
  sibling function) so they participate in the one repair round instead of only appearing
  after `build_from_audit`'s tail — closing the other half of F6.

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

- **New `repair_controller.py`**, replacing the ad-hoc logic currently inline in
  `_construct_with_correction`: a small state machine (not a framework) owning the bounded
  attempt budget (default 3, review's number, adjustable), the no-regression rule already
  partially implemented (extend "dropped features" to "any previously-passing
  `verify_contract` check regressing to FAIL or UNKNOWN"), and the stopping rule
  (`repeated candidate hash` or `no progress` — `revb_proposal.py` already tracks proposal
  hashes implicitly via `decode_proposal`; make the repeat check explicit).
- Final-verification pass: after the winning draft is chosen, export once more, reimport
  fresh, rerun `verify_contract` in full, and bind the report to that exact STEP's hash before
  marking anything reviewable — never reuse an in-memory result from the winning attempt.
- Model comparison harness: reuse `revb_accuracy.score_runs` plumbing, but point it at
  `verify_contract` results instead of (or alongside) the diagnostic scoreboard, across
  repeated runs per drawing per provider, logging actual provider/model version strings
  (`PipelineConfig` already threads model names through — add version logging at the
  `call_gemini` boundary).

## 3. File-level backlog (supersedes the review's table with current paths)

| Priority | File | Change |
|---|---|---|
| P0 | `web_api.py`, `legacy_web_api.py`, `vercel.json` | Expose builder/pipeline/commit + supported-geometry in health and manifests; decide the legacy path's fate. |
| P0 | new `datums.py` | Datum registry; `Numeric.datum` resolves through it, not to a literal zero. |
| P0 | new `source_contract.py` | `ContractDimension`/`ContractFeature`/`SourceContract`, immutable, hash-bound. |
| P0 | new `source_verification.py` (wraps `revb_geometry.py`) | `verify_contract`; generalizes `_axial_band`; adds shoulder/groove/connectivity handlers. |
| P0 | new `tests/test_source_verification.py` | The 40→80 mm bore probe and the full mutation battery, all as automatic regressions. |
| P1 | `revb_model.py` | `compound_port`: independent entry/follow-on axes, split depth semantics, explicit termination. |
| P1 | `revb_proposal.py` | Fix the profile-repair prompt contradiction; feed D-layer obligations into the repair round; crop-aware `Requester` variant. |
| P1 | `revb_sections.py` | Feature-aligned inspection planes; separate topology from metric conformance. |
| P1 | `revb_accuracy.py` | Add a `SourceContract`-driven conformance mode alongside the existing diagnostic scoreboard (kept, unchanged) — do not blend the two. |
| P1→P2 | new `repair_controller.py` | Attempt budget, strict no-regression against `verify_contract`, repeat-hash stop, final fresh-import re-verification. |
| P2 | `web_api.py`, review UI | Show integrity / source conformance / unresolved obligations / review state as separate fields, bound to the delivered STEP hash. |

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
