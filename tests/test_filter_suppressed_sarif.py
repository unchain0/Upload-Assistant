from scripts.filter_suppressed_sarif import filter_suppressed_results


def test_filter_suppressed_results_retains_only_unsuppressed_findings() -> (
    None
):
    payload = {
        "runs": [
            {
                "results": [
                    {"ruleId": "kept"},
                    {
                        "ruleId": "suppressed",
                        "suppressions": [{"kind": "inSource"}],
                    },
                ]
            }
        ]
    }

    assert filter_suppressed_results(payload) == {
        "runs": [{"results": [{"ruleId": "kept"}]}]
    }
