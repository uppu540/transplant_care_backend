import pytest
from app.ai.tools import execute_tool, get_daily_budget
from app.db import get_supabase, SupabaseClient, set_supabase_client


@pytest.fixture(autouse=True)
def clean_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    yield client


def test_get_medicines_tool():
    sb = get_supabase()
    pid = "patient-123"
    sb.table("medicines").insert(
        {
            "patient_id": pid,
            "name": "Tacrolimus",
            "dose": "1mg",
            "schedule_times": ["08:00:00", "20:00:00"],
            "active": True,
        }
    ).execute()

    res = execute_tool(pid, "get_medicines", {})
    assert len(res) == 1
    assert res[0]["name"] == "Tacrolimus"
    assert res[0]["dose"] == "1mg"


def test_log_dose_tool():
    pid = "patient-123"
    sb = get_supabase()
    med_res = sb.table("medicines").insert(
        {
            "patient_id": pid,
            "name": "Mycophenolate",
            "dose": "500mg",
            "schedule_times": ["09:00:00"],
            "active": True,
        }
    ).execute()
    med_id = med_res.data[0]["id"]

    res = execute_tool(
        pid,
        "log_dose",
        {
            "medicine_id": med_id,
            "scheduled_at": "2026-11-21T09:00:00Z",
            "status": "taken",
        },
    )
    assert res.get("ok") is True

    # Attempting to log with invalid status
    res_bad = execute_tool(
        pid,
        "log_dose",
        {
            "medicine_id": med_id,
            "scheduled_at": "2026-11-21T09:00:00Z",
            "status": "delete_dose",
        },
    )
    assert "error" in res_bad


def test_get_daily_budget_no_limits():
    pid = "patient-no-limits"
    budget = get_daily_budget(pid)
    assert budget["limits_set"] is False
    assert budget["limits"] is None
    assert budget["remaining"] is None


def test_get_daily_budget_with_doctor_limits():
    pid = "patient-with-limits"
    sb = get_supabase()
    sb.table("dietary_limits").insert(
        {
            "patient_id": pid,
            "potassium_mg": 2000,
            "sodium_mg": 2000,
            "protein_g": 60,
            "calories_kcal": 2000,
        }
    ).execute()

    # Log food
    execute_tool(
        pid,
        "log_food",
        {
            "description": "Lunch with dal and rice",
            "potassium_mg": 300,
            "sodium_mg": 500,
            "protein_g": 15,
            "calories_kcal": 400,
        },
    )

    budget = get_daily_budget(pid)
    assert budget["limits_set"] is True
    assert budget["consumed"]["potassium_mg"] == 300
    assert budget["consumed"]["sodium_mg"] == 500
    assert budget["remaining"]["potassium_mg"] == 1700
    assert budget["remaining"]["sodium_mg"] == 1500
