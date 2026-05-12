"""Potok frontend (`/client_api/*`) wrapper — DeviseTokenAuth via static headers.

Used for endpoints not exposed in the public v3 Bearer API, currently:
- POST /client_api/jobs/{job}/{applicant}/communication/communicate.json
  (sends a message to the candidate through HH or other channels)

Auth: 3 headers (access-token, client, uid) captured from browser DevTools.
Tokens observed stable — no rotation — but the underlying cookie expires
~every 6 weeks (re-extract on 401).
"""
import logging
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


def extract_hh_channels(accounts: list | None) -> list[str]:
    """Pick `t=<channel_id>` from every headhunter URL in accounts[]."""
    channels: list[str] = []
    for acc in accounts or []:
        if (acc or {}).get("icon_id") != "headhunter":
            continue
        url = (acc or {}).get("url") or ""
        q = parse_qs(urlparse(url).query)
        t = (q.get("t") or [None])[0]
        if t and t not in channels:
            channels.append(t)
    return channels


class PotokFrontendClient:
    def __init__(self):
        token = settings.potok_frontend_access_token.get_secret_value().strip()
        client_id = settings.potok_frontend_client.strip()
        uid = settings.potok_frontend_uid.strip()
        if not (token and client_id and uid):
            self._client = None
            logger.info("PotokFrontend: tokens not configured — frontend API disabled")
            return
        self._client = httpx.AsyncClient(
            base_url=settings.potok_base_url,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            headers={
                "access-token": token,
                "client": client_id,
                "uid": uid,
                "token-type": "Bearer",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": settings.potok_base_url,
                "User-Agent": "ArkadyJarvis/1.0",
            },
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def send_hh_message(
        self, job_id: int, applicant_id: int, channels: list[str], text: str,
    ) -> bool:
        """Send a single HH message to one or more channels in one call.
        Returns True on success."""
        if not self._client:
            logger.warning("PotokFrontend: send_hh_message called but client not configured")
            return False
        if not channels:
            return False

        payload = {
            "communication_envelopes": [{
                "provider": "headhunter",
                "channels": list(channels),
                "message": {"body": text},
            }]
        }
        try:
            r = await self._client.post(
                f"/client_api/jobs/{job_id}/{applicant_id}/communication/communicate.json",
                json=payload,
                headers={"Referer": f"{settings.potok_base_url}/j/{job_id}/all/a/{applicant_id}/"},
            )
            if r.status_code < 400:
                logger.info(
                    "PotokFrontend: HH sent to applicant %s via channels %s",
                    applicant_id, channels,
                )
                return True
            logger.error(
                "PotokFrontend: HH send -> %s for applicant %s: %s",
                r.status_code, applicant_id, r.text[:300],
            )
            return False
        except Exception as e:
            logger.error("PotokFrontend: HH send error for %s: %s", applicant_id, e)
            return False
