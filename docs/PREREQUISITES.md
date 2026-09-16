# Milestone 0: collect and record prerequisites

## Dataset

Collect 30–50 historical gland-ring drawings and the STEP files created by the engineer.
Choose 20 development cases and at least 10 held-out cases. Include clean exports and scans.
Use a stable `group` for related designs and revisions so they cannot cross the split.
Do not tune prompts using held-out examples.

Create `work/prerequisites.json` using the CLI. Add entries to `pairs`:

```json
{
  "id": "case-001",
  "owner": "shop-owner-id",
  "group": "related-design-family-id",
  "drawing": "dataset/case-001.pdf",
  "step": "dataset/case-001.step",
  "split": "development",
  "quality": "clean",
  "drawing_number": null,
  "revision": null,
  "synthetic": false
}
```

Paths above are examples, not files supplied with this repository. Read identity from the
title block and enter it when verified. Do not substitute the filename.

```sh
uv run drawing2step inventory work/prerequisites.json > work/inventory.json
```

Keep the inventory beside the source manifest so relative paths retain their meaning.
It contains `drawing_sha256` and `step_sha256` for every pair. Later runs verify recorded
hashes against the actual files instead of silently accepting altered originals.

## Permission records

Obtain written permission covering cloud processing from each drawing owner. A repository
checkbox is not that permission. Keep the actual evidence locally and record its scope in
the manifest's `permissions` array:

```json
{
  "owner": "shop-owner-id",
  "document": "permissions/owner-authorization.pdf",
  "confirmed_by": "Name of person who checked the permission",
  "cloud_processing_allowed": true,
  "drawing_sha256": ["replace-with-an-original-drawing-sha256-from-inventory"]
}
```

List every covered original hash. The tool checks owner/hash coverage and the presence of a
nonempty permission file. A human must confirm authenticity and scope; the tool cannot do so.
The inventory/readiness commands do not upload files. The separate `inspect-pdf --live`
diagnostic explicitly sends the chosen drawing to Gemini; use it only for authorized tests.

## Baseline and reviewers

Time the engineer's existing manual modeling workflow on 5–10 distinct real cases. Include
normal checking/export time. Add baseline records such as:

```json
{"pair_id": "case-001", "engineer": "Engineer name", "minutes": 42.5}
```

Set `gland_ring_workload_share` between 0 and 1 using observed incoming work. Values below
0.5 keep the prerequisite checklist blocked pending a scope reconsideration. Set the
`ground_truth_reviewer` name and the `release_reviewers` list.

Do not claim that historical STEP files are automatically correct. In the next milestone,
the engineer must adjudicate drawing/STEP discrepancies and label every requirement.

## CAD and environment decisions

Defaults are **provisional**: nominal geometry, mm, AP242, tap-drill holes with thread notes.
Record changes in `policies`. Capture the eventual engineer approval, target CAM product and
version, and CAM-import evidence before any manufacturing release.

The `environment` section records the selected region, retention days, exact frozen OCR
processor version, whether model versions were verified, and a path to configuration
evidence. These records are not checked against a live cloud account. Before provisioning,
verify regional model/OCR availability, data handling/retention, service accounts, encryption,
and audit logging. Put no credentials or secrets in the manifest.

Run `readiness` after updating the records. Its `next_gates` remain relevant even if the
local checklist reports `PREREQUISITES_RECORDED`.
