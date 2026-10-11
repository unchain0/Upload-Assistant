from src.domain_models.tracker_upload_state import TrackerUploadState
from src.engines.tracker_upload_eligibility import (
    eligible_tracker_names,
    evaluate_tracker_upload_eligibility,
)


def test_tracker_upload_eligibility_excludes_definitive_blockers() -> None:
    states = [
        TrackerUploadState("MIDNIGHTSCENE", upload_allowed=True),
        TrackerUploadState("DIGITALCORE", upload_allowed=False, dupe=True),
        TrackerUploadState(
            "YUSCENE",
            upload_allowed=False,
            skipped=True,
            reason="language rule",
        ),
        TrackerUploadState(
            "HDSPACE", upload_allowed=False, reason="invalid configuration"
        ),
    ]

    results = evaluate_tracker_upload_eligibility(states)

    assert eligible_tracker_names(states) == ("MIDNIGHTSCENE",)
    assert {
        result.tracker: result.reason
        for result in results
        if not result.eligible
    } == {
        "DIGITALCORE": "duplicate",
        "YUSCENE": "language rule",
        "HDSPACE": "invalid configuration",
    }


def test_tracker_upload_eligibili_prioritiz_banned_t_69bfc9() -> None:
    assert (
        evaluate_tracker_upload_eligibility(
            [TrackerUploadState("A", upload_allowed=True, banned=True)]
        )[0].reason
        == "banned"
    )
    assert (
        evaluate_tracker_upload_eligibility(
            [TrackerUploadState("A", upload_allowed=True, skipped=True)]
        )[0].reason
        == "skipped"
    )
    assert (
        evaluate_tracker_upload_eligibility(
            [TrackerUploadState("A", upload_allowed=True, dupe=True)]
        )[0].reason
        == "duplicate"
    )
    assert (
        evaluate_tracker_upload_eligibility(
            [TrackerUploadState("A", upload_allowed=False)]
        )[0].reason
        == "upload not allowed"
    )
