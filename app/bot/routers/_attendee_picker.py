"""Shared keyboards for the attendee-picker flow.

`meeting.py` and `free_slots.py` both drive a small interactive search
for Bitrix24 users: cancel button, status after a pick, and a list of
found-users buttons. The only differences between the two are:

- the `cancel` callback prefix (`mtg:cancel` vs `book:cancel`);
- the `done` button label and the action it triggers on `search:done`
  (book a meeting vs search free slots).

This module centralises the keyboards so both routers share the same
widgets instead of duplicating ~60 lines.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Callback data used for user-picked buttons in the results list. Both
# meeting and free_slots routers consume this prefix with their own
# StateFilter.
PICK_CB_PREFIX = "pick:"

# Shared add-me / more callbacks — safe to reuse because each router
# filters them by its own FSM state.
ADD_ME_CB = "search:addme"
MORE_CB = "search:more"
DONE_CB = "search:done"


def cancel_kb(cancel_cb: str, *, show_add_me: bool = True) -> InlineKeyboardMarkup:
    """Minimal keyboard — just "+ Я" (optional) and a cancel button."""
    rows: list[list[InlineKeyboardButton]] = []
    if show_add_me:
        rows.append([InlineKeyboardButton(text="+ Я", callback_data=ADD_ME_CB)])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_status_kb(
    cancel_cb: str,
    done_cb: str,
    done_label: str,
    *,
    show_add_me: bool = True,
) -> InlineKeyboardMarkup:
    """Keyboard shown after picking a user: more / <done_label> / cancel."""
    first_row = [
        InlineKeyboardButton(text="+ Ещё участник", callback_data=MORE_CB),
        InlineKeyboardButton(text=done_label, callback_data=done_cb),
    ]
    if show_add_me:
        first_row.insert(0, InlineKeyboardButton(text="+ Я", callback_data=ADD_ME_CB))
    rows = [first_row, [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_results_kb(users: list[dict], cancel_cb: str) -> InlineKeyboardMarkup:
    """List of found users as buttons + cancel."""
    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        rows.append([InlineKeyboardButton(
            text=u["name"],
            callback_data=f"{PICK_CB_PREFIX}{u['id']}:{u['name'][:40]}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
