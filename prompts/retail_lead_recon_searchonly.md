Сайт компании не открылся или не найден (Cloudflare / антибот / 5xx / таймаут). Собери максимум информации о компании **только через WebSearch** (без WebFetch).

**Компания**: {company}
**Человек (контакт с выставки)**: {person}
**Подсказка по сайту**: {site_hint}

**Лимит**: не более 4 WebSearch-вызовов суммарно. Что не нашёл — null/[], не выдумывай.

**Что искать**:
1. `{company}` + «официальный сайт» / «контакты»
2. `{company}` + «отзывы 2024 2025» / «новости 2025»
3. `{company}` + «отрасль конкуренты рынок»

**JSON** (без markdown-обёртки):
{
  "site_unreachable": true,
  "website": "сайт из выдачи или null",
  "company_name": "Бренд или название из выдачи",
  "industry": "...",
  "region": "...",
  "what_they_do": "Что нашёл из выдачи",
  "target_audience": null,
  "size_hint": null,
  "key_contacts": [],
  "phones": ["+7..."],
  "emails": ["info@..."],
  "social": {"vk": null, "telegram": null, "instagram": null},
  "news_hooks": ["..."],
  "pain_points_hypothesis": "1 фраза",
  "why_dc_can_help": "1-2 идеи",
  "industry_dynamics": null,
  "industry_research": [],
  "competitor_count_dynamics": null,
  "top_competitors": []
}

Что нашлось — заполни, остальное оставь null или []. Только JSON.
