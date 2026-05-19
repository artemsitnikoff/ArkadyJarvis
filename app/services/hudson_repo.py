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
DEFAULT_MANAGER_MAPPING: dict[str, list[str]] = {
    "Бешеля": ["Некрасова", "Константинова"],
    "Даниленко": [
        "Присяжнюк",  # был «Присняжнюк» — лишняя «н», в Bitrix без неё
        "Геливанов",
        "Овсянников",
        "Осицын",  # был «Осицины» — в Bitrix через «ы» в конце нет
        "Ушаков",
        "Кузнецова Юлия",  # Юля — Кузнецова, не путать с Кузнецовым у Васильевой
        "Сердюков",
    ],
    "Васильева": [
        "Казачок",
        "Гусев",
        "Кузнецов",
        "Попок",  # Филипп
        "Зеленских",  # Марк
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


async def seed_default_managers(bitrix, jira=None) -> tuple[int, list[str]]:
    """Заполняет hudson_managers из DEFAULT_MANAGER_MAPPING и резолвит ID+email
    через Bitrix по LAST_NAME. Если передан jira-клиент — резолвит jira_username
    по email. Возвращает (inserted_rows, unresolved_warnings)."""
    db = get_db()
    inserted = 0
    warnings: list[str] = []

    # Резолвим менеджеров отдельно (раз каждого): Bitrix ID + email + Jira-username
    manager_lookup: dict[str, tuple[int | None, str | None, str | None]] = {}
    for mgr_last in DEFAULT_MANAGER_MAPPING.keys():
        info = await _find_user_by_last_name(bitrix, mgr_last)
        if not info:
            warnings.append(f"Менеджер «{mgr_last}» не найден в Bitrix")
            manager_lookup[mgr_last] = (None, None, None)
            continue
        bx_id, email = info
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
        manager_lookup[mgr_last] = (bx_id, email, mgr_jira)

    for mgr_last, devs in DEFAULT_MANAGER_MAPPING.items():
        mgr_id, _mgr_email, mgr_jira = manager_lookup[mgr_last]
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
                dev_id, dev_email = dev_info

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
                        manager_jira_username)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(manager_name, developer_pattern) DO UPDATE SET
                       manager_bitrix_id = excluded.manager_bitrix_id,
                       developer_bitrix_id = excluded.developer_bitrix_id,
                       developer_email = excluded.developer_email,
                       jira_username = excluded.jira_username,
                       manager_jira_username = excluded.manager_jira_username""",
                (
                    mgr_last, mgr_id, dev_pattern, dev_id, dev_email,
                    jira_username, mgr_jira,
                ),
            )
            inserted += 1
    await db.commit()
    logger.info(
        "Hudson managers seed: %d rows, %d warnings", inserted, len(warnings),
    )
    return inserted, warnings


async def _find_user_by_last_name(bitrix, last_name: str) -> tuple[int, str | None] | None:
    """Bitrix user.get filter[LAST_NAME] — берём первого активного.
    Fallback на LIKE (%foo%): в Bitrix у некоторых пользователей LAST_NAME содержит
    trailing-пробел или другие артефакты — точный матч промахивается."""
    for value in (last_name, f"%{last_name}%"):
        try:
            r = await bitrix._request(
                "user.get",
                {"filter": {"LAST_NAME": value, "ACTIVE": True}},
            )
            users = r.get("result") or []
            if users:
                u = users[0]
                return int(u.get("ID")), (u.get("EMAIL") or None)
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
