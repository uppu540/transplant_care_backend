import asyncio

from app.ai.tools import execute_tool, get_daily_budget


def test_get_medicines_tool(mongo_repository):
    patient_id = "patient-123"
    asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {
                "name": "Tacrolimus",
                "dose": "1mg",
                "schedule_times": ["08:00:00", "20:00:00"],
                "active": True,
            },
        )
    )

    medicines = asyncio.run(
        execute_tool(patient_id, "get_medicines", {}, mongo_repository)
    )
    assert len(medicines) == 1
    assert medicines[0]["name"] == "Tacrolimus"
    assert medicines[0]["dose"] == "1mg"


def test_log_dose_tool(mongo_repository):
    patient_id = "patient-123"
    medicine = asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {
                "name": "Mycophenolate",
                "dose": "500mg",
                "schedule_times": ["09:00:00"],
                "active": True,
            },
        )
    )
    result = asyncio.run(
        execute_tool(
            patient_id,
            "log_dose",
            {
                "medicine_id": medicine["id"],
                "scheduled_at": "2026-11-21T09:00:00Z",
                "status": "taken",
            },
            mongo_repository,
        )
    )
    assert result["ok"] is True
    assert result["log"][0]["patient_id"] == patient_id

    bad_result = asyncio.run(
        execute_tool(
            patient_id,
            "log_dose",
            {
                "medicine_id": medicine["id"],
                "scheduled_at": "2026-11-21T09:00:00Z",
                "status": "delete_dose",
            },
            mongo_repository,
        )
    )
    assert "error" in bad_result


def test_get_daily_budget_no_limits(mongo_repository):
    budget = asyncio.run(get_daily_budget("patient-no-limits", mongo_repository))
    assert budget["limits_set"] is False
    assert budget["limits"] is None
    assert budget["remaining"] is None


def test_get_daily_budget_uses_only_stored_doctor_limits(mongo_repository):
    patient_id = "patient-with-limits"
    asyncio.run(
        mongo_repository.insert_patient_record(
            "dietary_limits",
            patient_id,
            {
                "potassium_mg": 2000,
                "sodium_mg": 2000,
                "protein_g": 60,
                "calories_kcal": 2000,
            },
        )
    )

    asyncio.run(
        execute_tool(
            patient_id,
            "log_food",
            {
                "description": "Lunch with dal and rice",
                "potassium_mg": 300,
                "sodium_mg": 500,
                "protein_g": 15,
                "calories_kcal": 400,
            },
            mongo_repository,
        )
    )
    budget = asyncio.run(get_daily_budget(patient_id, mongo_repository))

    assert budget["limits_set"] is True
    assert budget["consumed"]["potassium_mg"] == 300
    assert budget["consumed"]["sodium_mg"] == 500
    assert budget["remaining"]["potassium_mg"] == 1700
    assert budget["remaining"]["sodium_mg"] == 1500
