import logging

from cryptography.fernet import Fernet

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = settings.encryption_key
    if not key:
        key = Fernet.generate_key().decode()
        logger.warning(
            "ENCRYPTION_KEY not set — generated a random key. "
            "Add this to your .env to persist across restarts: ENCRYPTION_KEY=%s",
            key,
        )
        settings.encryption_key = key

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
