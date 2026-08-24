"""Мисис Хадсон — справочники: проекты из DCJ.xlsx + маппинг менеджер→разработчики.

DCJ.xlsx — справочник всех Jira-проектов компании (key, name, internal flag,
направление, категория). Парсится в SQLite таблицу `dcj_projects`.

hudson_managers — DC-specific знание: какой менеджер ведёт каких разработчиков
(в Bitrix эти связи отсутствуют — у групп разработки head=NULL). Сидится из
жёстко заданного маппинга, резолвится в Bitrix user IDs + emails по фамилии.
"""
import logging
from pathlib import Path

import openpyxl

from app.db import get_db

logger = logging.getLogger("arkadyjarvis")

# Дефолтный маппинг WEB-ПиК. Может быть расширен через UI / API позже.
# Реорг 2026-06: Даниленко удалён — все 7 его разработчиков перераспределены
# (Геливанов/Присяжнюк → Бешеля; Овсянников/Осицын/Сердюков/Ушаков → Кузнецова Юлия;
# сама Кузнецова Юлия выделена менеджером).
# 2026-08: Гусев и Зеленских (Марк) уволены — убраны из Васильевой.
DEFAULT_MANAGER_MAPPING: dict[str, list[str]] = {
    "Бешеля": [
        "Некрасова",
        "Константинова",
        "Геливанов",  # ← переведён от Даниленко
        "Присяжнюк",  # ← переведён от Даниленко; был «Присняжнюк» — лишняя «н», в Bitrix без неё
    ],
    # Юля Кузнецова выделена менеджером (была разработчиком у Даниленко).
    # Ключ двусоставный — резолв менеджера ниже разбивает на слова (как разработчиков).
    # «Кузнецова» (не путать с Кузнецовым у Васильевой) → её Bitrix ID.
    "Кузнецова Юлия": [
        "Овсянников",
        "Осицын",  # был «Осицины» — в Bitrix через «ы» в конце нет
        "Сердюков",
        "Ушаков",
    ],
    "Васильева": [
        "Казачок",
        "Кузнецов",
        "Попок",  # Филипп
    ],
    "Васькова": [
        "Скородумов",  # был «Скорадумов» — опечатка, в Bitrix через «о»
        "Маврин",
    ],
}


async def import_dcj_xlsx(path: str | Path = "DCJ.xlsx") -> tuple[int, int]:
    """Парсит DCJ.xlsx → таблица dcj_projects. Возвращает (inserted, updated)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"DCJ file not found: {p}")
    wb = openpyxl.load_workbook(str(p), data_only=True)
    ws = wb.active

    db = get_db()
    inserted = 0
    updated = 0
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        name, key, internal_str, direction, category = (row + (None,) * 5)[:5]
        if not key or not str(key).strip():
            continue
        key = str(key).strip()
        is_internal = 1 if (str(internal_str or "").strip().lower() == "да") else 0
        name = (name or "").strip()
        direction = (direction or "").strip() or None
        category = (category or "").strip() or None

        async with db.execute(
            "SELECT 1 FROM dcj_projects WHERE project_key = ?", (key,),
        ) as cur:
            exists = await cur.fetchone()
        await db.execute(
            """INSERT INTO dcj_projects (project_key, name, is_internal, direction, category, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(project_key) DO UPDATE SET
                   name = excluded.name,
                   is_internal = excluded.is_internal,
                   direction = excluded.direction,
                   category = excluded.category,
                   updated_at = datetime('now')""",
            (key, name, is_internal, direction, category),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    await db.commit()
    logger.info("DCJ import: inserted=%d updated=%d", inserted, updated)
    return inserted, updated


async def get_project(project_key: str) -> dict | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM dcj_projects WHERE project_key = ?", (project_key,),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def seed_default_managers(
    bitrix, jira=None,
) -> tuple[int, list[tuple[str, str]], list[str]]:
    """Заполняет hudson_managers из DEFAULT_MANAGER_MAPPING и резолвит ID+email
    через Bitrix по LAST_NAME. Если передан jira-клиент — резолвит jira_username
    по email. После сида делает реконсиляцию — удаляет пары (менеджер, разработчик),
    которых больше нет в маппинге (иначе перевод разработчика к другому менеджеру
    оставлял бы залипшую старую привязку — задвоение в аудите).
    Возвращает (upserted_rows, removed_pairs, unresolved_warnings)."""
    db = get_db()
    inserted = 0
    warnings: list[str] = []

    # Резолвим менеджеров отдельно: Bitrix ID + email + Jira-username + full name
    manager_lookup: dict[
        str, tuple[int | None, str | None, str | None, str | None]
    ] = {}
    for mgr_last in DEFAULT_MANAGER_MAPPING.keys():
        # Ключ менеджера может быть «Имя Фамилия» (напр. «Кузнецова Юлия») —
        # пробуем каждое слово как LAST_NAME, как и для разработчиков ниже.
        info = None
        for word in mgr_last.split():
            info = await _find_user_by_last_name(bitrix, word)
            if info:
                break
        if not info:
            warnings.append(f"Менеджер «{mgr_last}» не найден в Bitrix")
            manager_lookup[mgr_last] = (None, None, None, None)
            continue
        bx_id, email, full_name = info
        mgr_jira: str | None = None
        if jira and email:
            try:
                mgr_jira = await jira.find_user_by_email(email)
            except Exception as e:
                logger.warning("Jira lookup for manager %s failed: %s", mgr_last, e)
            if not mgr_jira:
                warnings.append(
                    f"Jira username менеджера {mgr_last} ({email}) не найден",
                )
        manager_lookup[mgr_last] = (bx_id, email, mgr_jira, full_name)

    for mgr_last, devs in DEFAULT_MANAGER_MAPPING.items():
        mgr_id, _mgr_email, mgr_jira, mgr_full = manager_lookup[mgr_last]
        for dev_pattern in devs:
            # Пытаемся каждое слово как LAST_NAME (вдруг в паттерне «Имя Фамилия»)
            dev_info = None
            for word in dev_pattern.split():
                dev_info = await _find_user_by_last_name(bitrix, word)
                if dev_info:
                    break
            if not dev_info:
                warnings.append(
                    f"Разработчик «{dev_pattern}» (мгр {mgr_last}) не найден в Bitrix"
                )
                dev_id, dev_email = None, None
            else:
                dev_id, dev_email, _dev_full = dev_info

            jira_username = None
            if jira and dev_email:
                try:
                    jira_username = await jira.find_user_by_email(dev_email)
                except Exception as e:
                    logger.warning("Jira lookup for %s failed: %s", dev_email, e)
                if not jira_username:
                    warnings.append(
                        f"Jira username для {dev_pattern} ({dev_email}) не найден"
                    )

            await db.execute(
                """INSERT INTO hudson_managers
                       (manager_name, manager_bitrix_id, developer_pattern,
                        developer_bitrix_id, developer_email, jira_username,
                        manager_jira_username, manager_full_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(manager_name, developer_pattern) DO UPDATE SET
                       manager_bitrix_id = excluded.manager_bitrix_id,
                       developer_bitrix_id = excluded.developer_bitrix_id,
                       developer_email = excluded.developer_email,
                       jira_username = excluded.jira_username,
                       manager_jira_username = excluded.manager_jira_username,
                       manager_full_name = excluded.manager_full_name""",
                (
                    mgr_last, mgr_id, dev_pattern, dev_id, dev_email,
                    jira_username, mgr_jira, mgr_full,
                ),
            )
            inserted += 1

    # Реконсиляция: сид — upsert, сам старые пары не удаляет. Сносим всё, чего
    # больше нет в DEFAULT_MANAGER_MAPPING (перераспределённые/убранные разработчики,
    # упразднённые менеджеры вроде Даниленко).
    desired_pairs = {
        (mgr, dev)
        for mgr, devs in DEFAULT_MANAGER_MAPPING.items()
        for dev in devs
    }
    async with db.execute(
        "SELECT manager_name, developer_pattern FROM hudson_managers"
    ) as cur:
        existing_pairs = {(r[0], r[1]) for r in await cur.fetchall()}
    stale_pairs = sorted(existing_pairs - desired_pairs)
    for mgr_name, dev_pat in stale_pairs:
        await db.execute(
            "DELETE FROM hudson_managers "
            "WHERE manager_name = ? AND developer_pattern = ?",
            (mgr_name, dev_pat),
        )

    await db.commit()
    logger.info(
        "Hudson managers seed: %d upserted, %d removed, %d warnings",
        inserted, len(stale_pairs), len(warnings),
    )
    if stale_pairs:
        logger.info(
            "Hudson reconcile removed: %s",
            ", ".join(f"{m}/{d}" for m, d in stale_pairs),
        )
    return inserted, stale_pairs, warnings


async def _find_user_by_last_name(
    bitrix, last_name: str,
) -> tuple[int, str | None, str | None] | None:
    """Bitrix user.get filter[LAST_NAME] — берём первого активного.
    Возвращает (id, email, full_name='Имя Фамилия')."""
    for value in (last_name, f"%{last_name}%"):
        try:
            r = await bitrix._request(
                "user.get",
                {"filter": {"LAST_NAME": value, "ACTIVE": True}},
            )
            users = r.get("result") or []
            if users:
                u = users[0]
                full = " ".join(
                    p for p in (u.get("NAME", ""), u.get("LAST_NAME", "")) if p
                ).strip()
                return int(u.get("ID")), (u.get("EMAIL") or None), (full or None)
        except Exception as e:
            logger.warning("Bitrix user.get LAST_NAME=%s failed: %s", value, e)
    return None


async def get_manager_for_developer(developer_pattern: str) -> dict | None:
    """Найти запись (manager + developer) по фамилии разработчика."""
    db = get_db()
    async with db.execute(
        "SELECT * FROM hudson_managers WHERE developer_pattern = ?",
        (developer_pattern,),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_managers() -> list[dict]:
    """Список уникальных менеджеров с их Bitrix ID."""
    db = get_db()
    async with db.execute(
        "SELECT manager_name, manager_bitrix_id, COUNT(*) as dev_count "
        "FROM hudson_managers GROUP BY manager_name",
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_developers() -> list[dict]:
    """Все разработчики с их менеджером."""
    db = get_db()
    async with db.execute("SELECT * FROM hudson_managers ORDER BY manager_name") as cur:
        return [dict(r) for r in await cur.fetchall()]
