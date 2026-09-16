from drawing2step.revb_evaluation import assess_dataset, score_inventory


def f(id, count):
    return {
        "id": id,
        "type": "hole_pattern",
        "view": "plan",
        "count": count,
        "box": [0, 0, 100, 100],
        "description": "holes",
    }


def test_missing_eighth_hole_reduces_recall():
    score = score_inventory([f("truth", 8)], [f("prediction", 7)])
    assert score["recall"] == 7 / 8
    assert score["missing_instances"] == 1


def test_empty_labels_never_pass():
    assert score_inventory([], [])["recall"] is None
    assert assess_dataset([])["status"] == "BLOCKED"


def test_family_revision_leakage_blocks():
    cases = [
        {
            "id": "a",
            "group": "family1",
            "split": "development",
            "synthetic": False,
            "approved_by": "engineer",
        },
        {
            "id": "b",
            "group": "family1",
            "split": "held_out",
            "synthetic": False,
            "approved_by": "engineer",
        },
    ]
    assert any("leakage" in x for x in assess_dataset(cases)["blockers"])


def test_synthetic_cases_cannot_unlock_accuracy_gate():
    cases = [
        {
            "id": str(i),
            "group": str(i),
            "split": "development" if i < 20 else "held_out",
            "synthetic": True,
            "approved_by": "engineer",
        }
        for i in range(30)
    ]
    assert assess_dataset(cases)["status"] == "BLOCKED"


def write_case(tmp_path, *, synthetic=False, audit_candidate="frozen", wrong_reference=False):
    import hashlib
    import json

    import cadquery as cq

    def artifact(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

    drawing = artifact("drawing.pdf", b"paired evaluation fixture")
    step = tmp_path / "reference.step"
    cq.exporters.export(
        cq.Workplane("XY").circle(50).circle(20).extrude(10 if wrong_reference else 20), str(step)
    )
    reference = {"path": step.name, "sha256": hashlib.sha256(step.read_bytes()).hexdigest()}
    truth = artifact(
        "truth.json",
        json.dumps(
            {
                "inventory": {"features": [f("holes", 8)]},
                "structure": [
                    {
                        "id": "body",
                        "kind": "axial_band",
                        "z0": 0,
                        "z1": 20,
                        "od": 100,
                        "idiameter": 40,
                    }
                ],
            }
        ).encode(),
    )
    audit = artifact(
        "audit.json",
        json.dumps(
            {
                "original_sha256": drawing["sha256"],
                "candidate_fingerprint": audit_candidate,
                "inventory": {"features": [f("holes", 8)]},
            }
        ).encode(),
    )
    return {
        "id": "case",
        "group": "family",
        "quality": "clean",
        "split": "held_out",
        "synthetic": synthetic,
        "approved_by": "test-reviewer",
        "approval_reason": "Synthetic test only",
        "drawing": drawing,
        "reference_step": reference,
        "truth": truth,
        "audit": audit,
        "seeded_errors": [],
    }


def test_synthetic_result_not_in_real_accuracy_metrics(tmp_path):
    import json

    from drawing2step.revb_evaluation import evaluate_manifest

    case = write_case(tmp_path, synthetic=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"frozen_candidate": "frozen", "cases": [case]}))
    result = evaluate_manifest(manifest)
    assert result["metrics"]["clean"]["inventory_recall"] is None
    assert result["status"] == "BLOCKED"


def test_wrong_reference_is_not_a_usable_baseline(tmp_path):
    import json

    from drawing2step.revb_evaluation import evaluate_manifest

    case = write_case(tmp_path, wrong_reference=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"frozen_candidate": "frozen", "cases": [case]}))
    result = evaluate_manifest(manifest)
    assert result["cases"] == []
    assert result["metrics"]["clean"]["structure_catch_rate"] is None


def test_stale_candidate_cannot_enter_metrics(tmp_path):
    import json

    from drawing2step.revb_evaluation import evaluate_manifest

    case = write_case(tmp_path, audit_candidate="old-model")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"frozen_candidate": "frozen", "cases": [case]}))
    result = evaluate_manifest(manifest)
    assert result["cases"] == []


def test_duplicate_drawing_hash_is_blocked(tmp_path):
    import json

    from drawing2step.revb_evaluation import evaluate_manifest

    case = write_case(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "frozen_candidate": "frozen",
                "cases": [case, {**case, "id": "duplicate", "group": "different"}],
            }
        )
    )
    result = evaluate_manifest(manifest)
    assert len(result["cases"]) == 1
    assert any("duplicate" in b for b in result["blockers"])
