Сайт компании НЕ открылся (Cloudflare / антибот / 5xx / таймаут). Тебе нужно собрать максимум информации **только через WebSearch** (без WebFetch).

**Домен**: {site}

**Лимит**: не более 4 WebSearch-вызовов суммарно. Если ничего не нашлось — возвращай что есть, null'и не выдумывай.

**Что искать**:
1. `<домен>` или название бренда + «контакты»
2. `<домен>` + «отзывы 2024 2025»
3. название бренда + «новости 2025»
4. название бренда + «отрасль конкуренты»

**JSON** (без markdown-обёртки):
{
  "site_unreachable": true,
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
