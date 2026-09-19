from fastapi import FastAPI, APIRouter, HTTPException, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import ipaddress
import logging
import time
import uuid
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, timezone
import httpx

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from database import client, db

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ["EMERGENT_EMAIL_KEY"]
EMAIL_FROM_NAME = os.environ["EMAIL_FROM_NAME"]
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")
OWNER_EMAIL = os.environ["OWNER_EMAIL"]

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str, reply_to: str | None = None) -> str | None:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if reply_to or EMAIL_REPLY_TO:
        payload["contact_email"] = reply_to or EMAIL_REPLY_TO
    try:
        async with httpx.AsyncClient(timeout=30) as client_http:
            resp = await client_http.post(
                f"{EMAIL_BASE_URL}/api/v1/email/send",
                headers={"X-Email-Key": EMAIL_KEY},
                json=payload,
            )
        resp.raise_for_status()
        return resp.json().get("id")
    except httpx.HTTPStatusError as e:
        logger.error(f"Email send failed: {e.response.status_code} {e.response.text}")
        raise HTTPException(status_code=502, detail="Failed to send email")
    except Exception as e:
        logger.error(f"Email send error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send email")


class EnquiryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(min_length=8, max_length=20)
    restaurant_name: str = Field(min_length=2, max_length=160)
    outlets: Optional[str] = Field(default="1 Outlet", max_length=40)
    message: Optional[str] = Field(default="", max_length=2000)


_rate_bucket: dict = {}


def _rate_limit(ip: str) -> None:
    now = time.time()
    hits = [t for t in _rate_bucket.get(ip, []) if now - t < 3600]
    if len(hits) >= 6:
        raise HTTPException(status_code=429, detail="Too many enquiries. Please try again later.")
    hits.append(now)
    _rate_bucket[ip] = hits


@api_router.get("/")
async def root():
    return {"message": "InventoryPro.in API"}


@api_router.post("/enquiry")
async def create_enquiry(input: EnquiryCreate, request: Request):
    _rate_limit(request.client.host if request.client else "unknown")
    doc = {
        "id": str(uuid.uuid4()),
        "name": input.name,
        "email": input.email,
        "phone": input.phone,
        "restaurant_name": input.restaurant_name,
        "outlets": input.outlets,
        "message": input.message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.enquiries.insert_one(doc)

    subject = f"New demo enquiry — {input.restaurant_name}"
    row = lambda label, value: (
        f'<tr><td style="padding:8px 16px 8px 0;color:#9CA3AF;font-size:13px;vertical-align:top">{label}</td>'
        f'<td style="padding:8px 0;color:#111827;font-size:14px;font-weight:600">{value}</td></tr>'
    )
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px;font-family:Arial,sans-serif;background:#FAF8F5">'
        '<p style="font-size:12px;letter-spacing:2px;color:#FF5722;font-weight:700;margin:0 0 8px">INVENTORYPRO.IN — WEBSITE ENQUIRY</p>'
        f'<h2 style="margin:0 0 16px;color:#111827;font-size:20px">{escape(input.restaurant_name)} wants a live demo</h2>'
        '<table role="presentation" cellpadding="0" cellspacing="0">'
        + row("Owner / Contact", escape(input.name))
        + row("Email", escape(input.email))
        + row("Phone / WhatsApp", escape(input.phone))
        + row("Restaurant / Brand", escape(input.restaurant_name))
        + row("Outlets", escape(input.outlets or "1 Outlet"))
        + row("Challenges", escape(input.message or "—"))
        + '</table>'
        '<p style="font-size:12px;color:#6B7280;margin-top:24px">Sent by InventoryPro.in enquiry notifications. '
        'Reply to this email to reach the enquirer directly.</p>'
        '</td></tr></table>'
    )
    email_id = await send_email(to=OWNER_EMAIL, subject=subject, html=html, reply_to=input.email)

    confirm_subject = "We've received your demo request — InventoryPro.in"
    confirm_html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px;font-family:Arial,sans-serif;background:#FAF8F5">'
        '<p style="font-size:12px;letter-spacing:2px;color:#FF5722;font-weight:700;margin:0 0 8px">INVENTORYPRO.IN</p>'
        f'<h2 style="margin:0 0 12px;color:#111827;font-size:20px">Thanks {escape(input.name)} — your demo request is in</h2>'
        f'<p style="color:#374151;font-size:14px;line-height:1.6">We received the enquiry for '
        f'<strong>{escape(input.restaurant_name)}</strong>. Sai will reach out shortly to schedule your live '
        'walkthrough of restaurant inventory, production and food-cost control.</p>'
        '<p style="color:#374151;font-size:14px;line-height:1.6">Want a faster response? Message us directly.</p>'
        '<p style="margin:20px 0"><a href="https://wa.me/919908659651" style="background:#10B981;color:#ffffff;'
        'text-decoration:none;padding:12px 24px;border-radius:24px;font-size:14px;font-weight:bold">Chat with Sai on WhatsApp</a></p>'
        '<p style="font-size:12px;color:#6B7280;margin-top:24px">Sent by InventoryPro.in — restaurant inventory, '
        'production &amp; food-cost control. You received this because you requested a demo on our website.</p>'
        '</td></tr></table>'
    )
    confirm_id = await send_email(to=input.email, subject=confirm_subject, html=confirm_html)
    return {"status": "success", "id": doc["id"], "email_id": email_id, "confirmation_id": confirm_id}


from auth import auth_router
from app_routes import app_router

api_router.include_router(auth_router)
api_router.include_router(app_router)

app.include_router(api_router)


async def _variance_alert_loop():
    import asyncio
    from datetime import datetime, timezone, timedelta
    while True:
        try:
            ist = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(ist)
            if now.hour == 8:
                settings = await db.settings.find_one({"id": "settings"}) or {}
                if settings.get("alert_enabled", True) and settings.get("last_alert_date") != now.date().isoformat():
                    from app_routes import build_alert_summary, send_alert_email
                    summary = await build_alert_summary()
                    if summary["crossed"]:
                        email_id = await send_alert_email(summary)
                        await db.settings.update_one({"id": "settings"}, {"$set": {"last_alert_date": now.date().isoformat()}}, upsert=True)
                        logger.info(f"Variance alert email sent: {email_id}")
            if (now.hour == 22 and now.minute >= 30) or now.hour == 23:
                settings = await db.settings.find_one({"id": "settings"}) or {}
                if settings.get("recap_enabled", True) and settings.get("last_recap_date") != now.date().isoformat():
                    from app_routes import build_recap_summary, send_recap_email
                    recap = await build_recap_summary()
                    email_id = await send_recap_email(recap)
                    await db.settings.update_one({"id": "settings"}, {"$set": {"last_recap_date": now.date().isoformat()}}, upsert=True)
                    logger.info(f"Evening recap email sent: {email_id}")
        except Exception as e:
            logger.error(f"Variance alert loop error: {e}")
        await asyncio.sleep(1800)


@app.on_event("startup")
async def startup_seed():
    import asyncio
    from seed import seed_all
    await seed_all()
    try:
        from storage import init_storage
        await init_storage()
        logger.info("Object storage initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")
    asyncio.create_task(_variance_alert_loop())

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
