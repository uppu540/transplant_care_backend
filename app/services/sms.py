from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_sms(phone: str, message: str) -> dict:
    """Send SMS via MSG91; fall back to Twilio if configured."""
    settings = get_settings()
    phone = phone.lstrip("+").replace(" ", "")

    if settings.msg91_auth_key:
        return _msg91(phone, message, settings)

    if settings.twilio_account_sid and settings.twilio_auth_token:
        return _twilio(phone, message, settings)

    logger.warning("No SMS provider configured; dry-run to %s", phone)
    return {"ok": True, "dry_run": True, "to": phone, "body": message}


def _msg91(phone: str, message: str, settings) -> dict:
    # Flow API / sendhttp — adjust to your MSG91 plan
    url = "https://control.msg91.com/api/v5/flow/"
    if settings.msg91_template_id:
        payload = {
            "template_id": settings.msg91_template_id,
            "short_url": "0",
            "recipients": [{"mobiles": phone, "var1": message[:200]}],
        }
        headers = {
            "authkey": settings.msg91_auth_key,
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=20) as client:
            r = client.post(url, json=payload, headers=headers)
            return {"ok": r.is_success, "provider": "msg91", "status": r.status_code, "body": r.text}

    # Simple send SMS fallback
    with httpx.Client(timeout=20) as client:
        r = client.post(
            "https://api.msg91.com/api/v2/sendsms",
            headers={
                "authkey": settings.msg91_auth_key,
                "Content-Type": "application/json",
            },
            json={
                "sender": settings.msg91_sender_id,
                "route": "4",
                "country": "91",
                "sms": [{"message": message, "to": [phone]}],
            },
        )
        return {"ok": r.is_success, "provider": "msg91", "status": r.status_code, "body": r.text}


def _twilio(phone: str, message: str, settings) -> dict:
    from_number = settings.twilio_from_number
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Messages.json"
    )
    with httpx.Client(timeout=20) as client:
        r = client.post(
            url,
            data={"To": f"+{phone}", "From": from_number, "Body": message},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
        return {"ok": r.is_success, "provider": "twilio", "status": r.status_code, "body": r.text}
