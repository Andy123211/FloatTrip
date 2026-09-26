"""Short daily themes derived from the day's attractions, without changing routes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


# Specific place types precede broad ones (a zoo is not just another park).
_CATEGORIES = (
    ("wildlife", "自然寻趣", ("动物园", "野生动物", "海洋馆", "水族馆", "zoo", "aquarium")),
    ("garden", "园林寻幽", ("园林", "大观园", "拙政园", "留园", "豫园", "颐和园", "圆明园", "狮子林", "garden")),
    ("science", "科学探索", ("科技馆", "科学馆", "天文馆", "science")),
    ("art", "艺术漫游", ("美术馆", "艺术馆", "艺术区", "画廊", "雕塑", "gallery", "art")),
    ("museum", "博物寻知", ("博物馆", "博物院", "纪念馆", "展览馆", "museum")),
    ("heritage", "老城慢游", ("古镇", "古城", "古村", "古街", "老街", "老城", "历史街区", "故居", "遗址", "古迹", "城墙", "城隍", "寺", "庙", "宫殿", "history", "heritage")),
    ("play", "乐园畅游", ("游乐", "乐园", "迪士尼", "欢乐谷", "主题公园", "theme park")),
    ("food", "街巷寻味", ("小吃", "美食", "夜市", "餐饮街", "food")),
    ("coast", "海岸漫步", ("海滩", "沙滩", "海滨", "海岸", "滨海", "beach", "coast")),
    ("nature", "山水漫游", ("森林", "湿地", "峡谷", "瀑布", "山岳", "山峰", "国家公园", "湖", "nature", "hiking")),
    ("green", "绿意漫步", ("植物园", "公园", "绿道", "park")),
    ("city", "城市漫游", ("步行街", "商业街", "广场", "地标", "观景台", "外滩", "city", "commercial")),
)
_LABELS = {category: label for category, label, _ in _CATEGORIES}
_PAIRS = {
    frozenset(("heritage", "museum")): "老城人文漫游",
    frozenset(("heritage", "garden")): "古镇园林寻幽",
    frozenset(("heritage", "wildlife")): "自然寻趣·老街慢游",
    frozenset(("heritage", "food")): "老城烟火寻味",
    frozenset(("art", "museum")): "城市文化漫游",
    frozenset(("science", "museum")): "博物科学探索",
    frozenset(("green", "garden")): "园林绿意漫游",
    frozenset(("green", "wildlife")): "自然探索之旅",
}


def _category(spot: Mapping[str, Any]) -> str | None:
    name = str(spot.get("name") or spot.get("poi_name") or "").lower()
    metadata = " ".join((
        str(spot.get("poi_type") or spot.get("type") or ""),
        str(spot.get("category") or ""),
        " ".join(str(tag) for tag in spot.get("semantic_tags") or []),
    )).lower()
    # Prefer the actual attraction's name to a provider's generic POI type.
    for text in (name, metadata):
        for category, _, keywords in _CATEGORIES:
            if any(keyword in text for keyword in keywords):
                return category
    return None


def build_day_theme(spots: Sequence[Mapping[str, Any]]) -> str:
    """Use all distinct attractions; ties follow visit order for stable naming."""
    categories: Counter[str] = Counter()
    seen: set[str] = set()
    for spot in spots:
        name = str(spot.get("name") or spot.get("poi_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        category = _category(spot)
        if category:
            categories[category] += 1
    leaders = [category for category, _ in categories.most_common(2)]
    if not leaders:
        return "城市漫游" if spots else "自由探索"
    if len(leaders) == 1:
        return _LABELS[leaders[0]]
    return _PAIRS.get(frozenset(leaders)) or "·".join(_LABELS[category] for category in leaders)


def project_legacy_day_themes(plan: dict[str, Any]) -> None:
    """Normalize only missing/old machine titles on an already-copied read model.

    Old optimizer titles exactly joined the first two distinct spot names with
    与. Do not guess from the presence of 与 alone: authored themes may use it.
    No persistence or route mutation occurs here.
    """
    overrides = plan.get("day_themes") or {}
    for day in plan.get("days") or []:
        if str(day.get("day")) in overrides:
            continue
        spots = [item for item in day.get("timeline") or [] if item.get("type") == "attraction"]
        old_title = "与".join(dict.fromkeys(str(spot.get("name") or "") for spot in spots[:2]))
        title = str(day.get("theme") or "").strip()
        if not title or title == old_title:
            day["theme"] = build_day_theme(spots)
