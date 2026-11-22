import asyncio

from app.auth import AuthUser
from app.routers.labs import (
    LabValueInput,
    ManualReportInput,
    _extract_from_text,
    get_lab_trends,
    list_reports,
    save_manual_labs,
)


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


def test_manual_labs_and_trends(mongo_repository, profile_factory):
    profile = profile_factory("user-labs-patient", full_name="Kavitha")
    user = AuthUser(user_id="user-labs-patient")

    first = ManualReportInput(
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
    result_one = asyncio.run(
        save_manual_labs(first, user=user, repository=mongo_repository)
    )
    assert result_one["ok"] is True
    assert len(result_one["values"]) == 2

    second = ManualReportInput(
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
                value=5.3,
                ref_low=3.5,
                ref_high=5.0,
                measured_at="2026-11-15",
            ),
        ],
    )
    result_two = asyncio.run(
        save_manual_labs(second, user=user, repository=mongo_repository)
    )
    assert result_two["ok"] is True

    trends_result = asyncio.run(
        get_lab_trends(user=user, repository=mongo_repository)
    )
    trends = trends_result["trends"]
    assert len(trends["creatinine"]) == 2
    assert len(trends["potassium"]) == 2
    assert trends["potassium"][0]["out_of_range"] is False
    assert trends["potassium"][1]["out_of_range"] is True

    reports = asyncio.run(list_reports(user=user, repository=mongo_repository))
    assert len(reports["reports"]) == 2
