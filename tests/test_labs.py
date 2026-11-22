import pytest
from app.db import get_supabase, SupabaseClient, set_supabase_client
from app.routers.labs import (
    _extract_from_text,
    get_lab_trends,
    save_manual_labs,
    list_reports,
    ManualReportInput,
    LabValueInput,
)
from app.auth import AuthUser


@pytest.fixture(autouse=True)
def setup_mock_db():
    client = SupabaseClient("http://mock.supabase.co", "mock-key")
    set_supabase_client(client)
    yield client


def test_heuristic_lab_extraction():
    sample_text = """
    LAL PATH LABS - CLINICAL BIOCHEMISTRY REPORT
    Patient: Ramesh Babu, Age: 48, Male
    Test Name                Result    Reference Range    Unit
    Serum Creatinine         1.8       0.6 - 1.2          mg/dL
    Estimated GFR            45        >= 60              mL/min/1.73m2
    Serum Potassium          5.6       3.5 - 5.0          mEq/L
    Tacrolimus (FK506)       12.4      5.0 - 10.0         ng/mL
    Hemoglobin               10.2      12.0 - 16.0        g/dL
    """
    extracted = _extract_from_text(sample_text)
    analytes = {item["analyte"]: item["value"] for item in extracted}

    assert analytes["creatinine"] == 1.8
    assert analytes["egfr"] == 45.0
    assert analytes["potassium"] == 5.6
    assert analytes["tacrolimus"] == 12.4
    assert analytes["hemoglobin"] == 10.2


def test_manual_labs_and_trends():
    sb = get_supabase()
    user_id = "user-labs-patient"
    prof_res = sb.table("profiles").insert(
        {"user_id": user_id, "full_name": "Kavitha"}
    ).execute()
    pid = prof_res.data[0]["id"]
    user = AuthUser(user_id=user_id)

    # Insert historical lab report
    manual_input = ManualReportInput(
        collected_at="2026-11-01",
        values=[
            LabValueInput(
                analyte="creatinine",
                value=1.4,
                ref_low=0.6,
                ref_high=1.2,
                measured_at="2026-11-01",
            ),
            LabValueInput(
                analyte="potassium",
                value=4.2,
                ref_low=3.5,
                ref_high=5.0,
                measured_at="2026-11-01",
            ),
        ],
    )
    res1 = save_manual_labs(manual_input, user=user)
    assert res1["ok"] is True
    assert len(res1["values"]) == 2

    # Insert second lab report
    manual_input_2 = ManualReportInput(
        collected_at="2026-11-15",
        values=[
            LabValueInput(
                analyte="creatinine",
                value=1.1,
                ref_low=0.6,
                ref_high=1.2,
                measured_at="2026-11-15",
            ),
            LabValueInput(
                analyte="potassium",
                value=5.3,  # Out of range!
                ref_low=3.5,
                ref_high=5.0,
                measured_at="2026-11-15",
            ),
        ],
    )
    res2 = save_manual_labs(manual_input_2, user=user)
    assert res2["ok"] is True

    # Query trends
    trends_res = get_lab_trends(user=user)
    trends = trends_res["trends"]
    assert "creatinine" in trends
    assert "potassium" in trends
    assert len(trends["creatinine"]) == 2
    assert len(trends["potassium"]) == 2

    # Verify out of range flag
    k_trends = trends["potassium"]
    assert k_trends[0]["out_of_range"] is False  # 4.2
    assert k_trends[1]["out_of_range"] is True   # 5.3

    # List reports
    reps = list_reports(user=user)
    assert len(reps["reports"]) == 2
