from app.services.bitrix_client._base import _BitrixBase
from app.services.bitrix_client._calendar import _BitrixCalendarMixin
from app.services.bitrix_client._crm import _BitrixCRMMixin
from app.services.bitrix_client._users import _BitrixUsersMixin


class BitrixClient(_BitrixBase, _BitrixUsersMixin, _BitrixCalendarMixin, _BitrixCRMMixin):
    """Bitrix24 client — users, calendar, CRM."""

    def __init__(self):
        super().__init__()
        self._email_guests_cache: dict[str, tuple[int, str]] = {}
        self._email_guests_loaded = False
