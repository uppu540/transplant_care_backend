import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.auth import AuthUser
from app.routers.labs import download_lab_report, extract_labs
from app.storage import LabReportStorage


def test_lab_report_files_are_private_and_authenticated(
    mongo_database,
    mongo_repository,
    profile_factory,
):
    patient_a = profile_factory("patient-a", full_name="Patient A")
    profile_factory("patient-b", full_name="Patient B")
    upload = UploadFile(
        filename="lab-report.txt",
        file=BytesIO(b"Serum Creatinine 1.8"),
        headers=Headers({"content-type": "text/plain"}),
    )

    async def scenario():
        storage = LabReportStorage(mongo_database)
        extracted = await extract_labs(
            file=upload,
            report_text=None,
            user=AuthUser(user_id="patient-a"),
            repository=mongo_repository,
            storage=storage,
        )
        assert extracted["ok"] is True

        response = await download_lab_report(
            report_id=extracted["report_id"],
            user=AuthUser(user_id="patient-a"),
            repository=mongo_repository,
            storage=storage,
        )
        assert response.body == b"Serum Creatinine 1.8"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-disposition"].startswith("attachment;")

        with pytest.raises(HTTPException) as exc:
            await download_lab_report(
                report_id=extracted["report_id"],
                user=AuthUser(user_id="patient-b"),
                repository=mongo_repository,
                storage=storage,
            )
        assert exc.value.status_code == 404

    asyncio.run(scenario())
    assert patient_a["id"] != "patient-b"
