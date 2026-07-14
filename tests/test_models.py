from src.models import AgentContext, APIResponse, DaySummary, SessionRow, row_to_dict


def test_public_models_have_independent_collection_defaults():
    first = DaySummary(date="2026-07-14", total_seconds=1)
    second = DaySummary(date="2026-07-14", total_seconds=2)
    first.top_processes.append({"process_name": "Code.exe", "total_seconds": 1})

    assert second.top_processes == []
    assert SessionRow(id=1, start_time="x").is_active is True
    assert AgentContext().last_7_days == []
    assert APIResponse(data={"ok": True}).data == {"ok": True}
    assert row_to_dict(None) is None
