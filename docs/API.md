# TransplantCare API

The mobile app uses this FastAPI service as its only remote data source.
Interactive OpenAPI documentation is available at `/docs`.

## Base URL and authentication

The production base URL is `https://transplant-care-backend.vercel.app`.
Local development may use `http://10.0.2.2:8000` from an Android emulator.

Successful registration and login return `access_token`, `refresh_token`,
`token_type`, `expires_in`, `user_id`, `role`, `has_consent`, and
`onboarding_complete`. Send the access token as `Authorization: Bearer <token>`.
Access tokens are short-lived. `POST /auth/refresh` accepts a refresh token and
rotates it; `POST /auth/logout` revokes the active refresh token.

`GET /me/status` is available to authenticated users before consent and returns
`role`, `has_consent`, and `onboarding_complete`. Other clinical data routes
require consent. Legacy profiles without an onboarding marker are considered
complete; newly registered profiles start incomplete.

## Patient profile and onboarding

| Method | Path | Purpose |
|---|---|---|
| GET | `/me/profile` | Read the authenticated patient's profile |
| PATCH | `/me/profile` | Update name, transplant date, doctor details, and locale |
| POST | `/me/onboarding/complete` | Mark onboarding complete after its writes succeed |
| GET / PUT | `/me/emergency-contacts` | List or replace up to five contacts |
| GET / PUT | `/me/dietary-limits` | Read or set doctor-provided nutrient limits |
| POST | `/me/consent` | Record the accepted policy version |
| GET | `/me/summary` | Profile, medicines, today's logs, contacts, and daily budget |
| GET | `/me/budget/today` | Current daily limits, consumption, and remaining budget |
| GET | `/me/export` | Export patient-owned records |
| DELETE | `/me` | Delete the authenticated patient's account and records |

Emergency contacts use `{ "contacts": [{ "full_name": "...", "phone": "...",
"relation": "..." }] }`. Dietary limits accept nullable numeric fields named
`potassium_mg`, `sodium_mg`, `phosphorus_mg`, `protein_g`, `fluid_ml`, and
`calories_kcal`.

## Medicines and food

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/medicines` | List active medicines or create one |
| PATCH / DELETE | `/medicines/{medicine_id}` | Update or deactivate an owned medicine |
| POST | `/medicines/{medicine_id}/log` | Record a dose with `status` and `scheduled_at` |
| GET | `/medicines/adherence` | Read recent dose logs and adherence totals |
| POST | `/medicines/check-missed` | Check overdue doses and notify contacts |
| POST | `/food/estimate` | Estimate nutrients from `{ "text": "..." }` |
| POST | `/food/log` | Save an estimated or manual food log |
| GET | `/food/logs` | Read the authenticated user's food-log history |
| DELETE | `/food/logs/{log_id}` | Delete an owned food log |
| GET | `/food/suggestions` | Suggest foods against doctor-set limits |

Medicine schedules accept `HH:MM` or `HH:MM:SS` values. Dose logs use the
medicine-specific path and are scoped to the authenticated patient's profile.

## Lab reports

| Method | Path | Purpose |
|---|---|---|
| POST | `/labs/extract` | Multipart `file` upload or form `report_text`; saves the report and extracted values |
| POST | `/labs/manual` | Save confirmed values or replace values for an uploaded `report_id` |
| GET | `/labs/trends` | Read values grouped by analyte |
| GET | `/labs/reports` | List the patient's lab reports |
| GET | `/labs/reports/{report_id}/file` | Download an owned report file |

Manual lab values use a list of `{ "analyte": "...", "value": 1.2,
"unit": "..." }` objects and an optional `collected_at` date. To confirm or
correct extraction results, include the returned `report_id`; replacement is
limited to that patient's values for that report.

## Caregiver and emergency routes

Caregiver endpoints are `POST /me/caregiver/invite`,
`POST /me/caregiver/accept`, `GET /me/caregiver/patients`, and
`GET /me/caregiver/patient/{patient_id}`. Patient summaries are only returned
to an active linked caregiver. The detail response includes patient profile
contact information, medicines, today's dose logs, budget, and recent labs.

`POST /doctor/whatsapp-payload` returns a `wa_url` and message text.
`POST /sos/trigger` records the emergency event and returns per-contact
`sms_results`, `delivery_status`, WhatsApp links, and a dial URI. The status is
`sent`, `partial`, `failed`, or `dry_run`; clients must not report a dry-run or
failed SMS as delivered. `POST /sos/cancel` records cancellation of the latest
event.

The AI endpoint is `POST /ai/chat`. It returns a reply, red-flag indicator, and
tools used.

## Error and ownership behavior

Validation errors return HTTP 422, unauthenticated requests return 401, and
ownership failures do not reveal another patient's records. Clients should
display backend validation details for client errors and use a generic retry
message for server errors. Database credentials and signing secrets are
server-only environment configuration and must never be embedded in the app.
