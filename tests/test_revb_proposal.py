"""geometry_feedback: the repair prompt must not contradict itself."""

from drawing2step.revb_model import DraftSpec
from drawing2step.revb_proposal import SPEC_PROMPT, geometry_feedback


def _spec() -> DraftSpec:
    return DraftSpec.model_validate(
        {
            "reference_face": "face_A",
            "coordinate_policy": "Z increases from face A",
            "profile": [
                {"z": {"datum": "face_a"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "OD/2"}},
                {"z": {"ledger": "H"}, "radius": {"expr": "ID/2"}},
                {"z": {"datum": "face_a"}, "radius": {"expr": "ID/2"}},
            ],
            "features": [],
        }
    )


def test_feedback_never_tells_the_model_to_keep_a_profile_it_just_named_as_failing():
    prompt = geometry_feedback(_spec(), [("profile", "Body axial length is wrong")])
    assert "profile" in prompt.lower()
    assert "keep every other feature, the profile" not in prompt
    assert "correct its vertices (and only the profile)" in prompt


def test_feedback_still_protects_the_profile_when_only_features_failed():
    prompt = geometry_feedback(_spec(), [("bolts", "Entry face is not exposed")])
    assert "Keep every other feature, the profile and the assumptions exactly as before" in prompt


def test_spec_prompt_forbids_borrowing_another_features_angle():
    assert "Never reuse an angle printed for a different pattern or for a port" in SPEC_PROMPT


def test_spec_prompt_warns_against_defaulting_to_a_bare_cardinal_angle():
    flattened = " ".join(SPEC_PROMPT.split())
    assert "never default to a bare cardinal" in flattened
    assert "straddling a centreline" in flattened
