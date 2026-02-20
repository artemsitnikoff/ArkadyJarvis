import json
import time
from pathlib import Path

from fastapi import APIRouter

from app.config import settings
from app.db import get_db

router = APIRouter()

TOKENS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "bitrix_tokens.json"


@router.get("/health")
async def health():
    checks: dict = {}

    # DB check
    try:
        db = get_db()
        async with db.execute("SELECT 1") as cur:
            await cur.fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Bitrix token expiry check
    try:
        if TOKENS_FILE.exists():
            tokens = json.loads(TOKENS_FILE.read_text())
            expires_at = tokens.get("expires_at", 0)
            remaining = expires_at - int(time.time())
            checks["bitrix_token"] = "ok" if remaining > 60 else f"expires in {remaining}s"
        else:
            checks["bitrix_token"] = "no token file"
    except Exception as e:
        checks["bitrix_token"] = f"error: {e}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}
