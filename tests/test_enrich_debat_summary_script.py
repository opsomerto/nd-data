from scripts.enrich_debat_summary import existing_summary_needs_processing, input_ratio_percent


def test_existing_summary_needs_processing_respects_model_version_and_force():
    existing = {"model_name": "model-a", "agent_version": "1.0"}

    assert not existing_summary_needs_processing(existing, "model-a", force=False)
    assert existing_summary_needs_processing(existing, "model-b", force=False)
    assert existing_summary_needs_processing(existing, "model-a", force=True)
    assert existing_summary_needs_processing(None, "model-a", force=False)


def test_input_ratio_percent_handles_full_and_truncated_inputs():
    assert input_ratio_percent(1000, 250) == 25.0
    assert input_ratio_percent(0, 0) == 100.0
