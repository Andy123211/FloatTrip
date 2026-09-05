"""Authoritative candidate-pool assembly around LLM travel semantics."""

from __future__ import annotations

import re
import hashlib
import json
from typing import Any, Iterable, Sequence

from app.planning.helpers import cluster_pois_by_location
from app.planning.optimizer import candidate_pool_limit
from app.planning.schemas import CandidateAttraction, OptimizerCandidate
from app.planning.semantics import (
    authoritative_poi_index,
    is_featured_food_street,
    is_food_street,
    link_candidate_to_poi,
)


def _matching_names(text: str, names: Iterable[str]) -> list[str]:
    normalized = text.replace(" ", "")
    return [name for name in names if name and name.replace(" ", "") in normalized]


def excluded_poi_names(
    pois: Sequence[dict[str, Any]], constraints: Sequence[dict[str, Any]]
) -> set[str]:
    names = [str(poi.get("name") or "") for poi in pois]
    excluded: set[str] = set()
    for item in constraints:
        if item.get("polarity") != "avoid":
            continue
        excluded.update(_matching_names(str(item.get("value_text") or ""), names))
    return excluded


def _constraint_flags(
    poi_name: str,
    all_names: Sequence[str],
    constraints: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "must_visit": False,
        "fixed_day": None,
        "fixed_start_min": None,
        "must_be_first": False,
        "must_be_last": False,
        "before_poi_names": [],
        "evidence_constraint_ids": [],
    }
    for item in constraints:
        text = str(item.get("value_text") or "")
        matched = _matching_names(text, all_names)
        if poi_name not in matched:
            continue
        constraint_id = str(item.get("id") or item.get("fact_id") or "").strip()
        if constraint_id:
            flags["evidence_constraint_ids"].append(constraint_id)
        polarity = str(item.get("polarity") or "prefer")
        if polarity == "require" and any(token in text for token in ("必去", "必须", "一定要", "不能删")):
            flags["must_visit"] = True
        day_match = re.search(r"(?:第\s*(\d+)\s*天|Day\s*(\d+))", text, re.IGNORECASE)
        if day_match:
            flags["fixed_day"] = int(day_match.group(1) or day_match.group(2))
        time_match = re.search(r"(?<!\d)([0-2]?\d):([0-5]\d)", text)
        if time_match and any(token in text for token in ("预约", "固定", "准时", "开始")):
            flags["fixed_start_min"] = int(time_match.group(1)) * 60 + int(time_match.group(2))
        if any(token in text for token in ("首站", "第一站", "最先")):
            flags["must_be_first"] = True
        if any(token in text for token in ("末站", "最后一站", "最后去")):
            flags["must_be_last"] = True
        if "之前" in text or "先于" in text:
            for other in matched:
                if other == poi_name:
                    continue
                poi_pos = text.find(poi_name)
                other_pos = text.find(other)
                before_token = text.find("之前")
                if poi_pos >= 0 and (other_pos < 0 or poi_pos < other_pos or poi_pos < before_token):
                    flags["before_poi_names"].append(other)
    flags["evidence_constraint_ids"] = list(dict.fromkeys(flags["evidence_constraint_ids"]))
    flags["before_poi_names"] = list(dict.fromkeys(flags["before_poi_names"]))
    return flags


def _default_proposal(poi: dict[str, Any], preference: str | None) -> CandidateAttraction:
    name = str(poi.get("name") or "")
    pref_tokens = [token for token in re.split(r"[、,，；;\s]+", preference or "") if len(token) >= 2]
    preference_match = 0.7 if any(token in name or token in str(poi.get("type") or "") for token in pref_tokens) else 0.5
    rating = float(poi.get("rating") or 0)
    return CandidateAttraction(
        poi_name=name,
        duration_min=120,
        preference_match=preference_match,
        representativeness=max(0.3, min(1.0, rating / 5 if rating else 0.5)),
        preferred_period="any",
        meal_scene="none",
    )


def build_authoritative_candidates(
    proposals: Sequence[CandidateAttraction | dict[str, Any]],
    pois: Sequence[dict[str, Any]],
    *,
    days: int,
    constraints: Sequence[dict[str, Any]] = (),
    attraction_preference: str | None = None,
    food_focused: bool = False,
) -> tuple[list[OptimizerCandidate], list[str]]:
    """Drop hallucinations, fill the target pool, and enforce server semantics."""

    index = authoritative_poi_index(pois)
    excluded = excluded_poi_names(pois, constraints)
    eligible = [poi for name, poi in index.items() if name not in excluded]
    cluster_map = cluster_pois_by_location(eligible, max(1, days))
    warnings: list[str] = []
    linked: dict[str, OptimizerCandidate] = {}
    for raw in proposals:
        try:
            proposal = raw if isinstance(raw, CandidateAttraction) else CandidateAttraction.model_validate(raw)
        except Exception as exc:
            warnings.append(f"INVALID_LLM_CANDIDATE:{type(exc).__name__}")
            continue
        poi = index.get(proposal.poi_name)
        if poi is None or proposal.poi_name in excluded:
            warnings.append(f"UNKNOWN_OR_EXCLUDED_POI:{proposal.poi_name}")
            continue
        linked[proposal.poi_name] = link_candidate_to_poi(
            proposal, poi, cluster_id=cluster_map.get(proposal.poi_name)
        )

    # Food streets are meal destinations, not default core attractions.  In a
    # sightseeing-led trip retain at most one well-rated local landmark; a
    # user may still explicitly require a named street or choose food focus.
    required_names = {
        name
        for item in constraints
        if item.get("polarity") == "require"
        for name in _matching_names(str(item.get("value_text") or ""), index)
    }
    allowed_food = set(required_names)
    if not food_focused:
        featured = [
            candidate for candidate in linked.values()
            if is_featured_food_street(index[candidate.poi_name])
        ]
        if featured:
            allowed_food.add(max(featured, key=_candidate_rank).poi_name)
        elif not allowed_food:
            featured_pois = [poi for poi in eligible if is_featured_food_street(poi)]
            if featured_pois:
                allowed_food.add(str(max(
                    featured_pois,
                    key=lambda poi: float(poi.get("rating") or 0),
                ).get("name") or ""))
        for name in list(linked):
            if is_food_street(index[name]) and name not in allowed_food:
                linked.pop(name)
                warnings.append(f"FOOD_STREET_DEPRIORITIZED:{name}")

    desired = min(candidate_pool_limit(days), len(eligible))
    filler_eligible = eligible if food_focused else [
        poi for poi in eligible
        if not is_food_street(poi) or str(poi.get("name") or "") in allowed_food
    ]
    fillers = sorted(
        filler_eligible,
        key=lambda poi: (-float(poi.get("rating") or 0), str(poi.get("name") or "")),
    )
    for poi in fillers:
        name = str(poi.get("name") or "")
        if len(linked) >= desired:
            break
        if name not in linked:
            proposal = _default_proposal(poi, attraction_preference)
            linked[name] = link_candidate_to_poi(proposal, poi, cluster_id=cluster_map.get(name))
            warnings.append(f"SERVER_FILLED_CANDIDATE:{name}")

    all_names = list(index)
    for name, poi in index.items():
        flags = _constraint_flags(name, all_names, constraints)
        if not flags["must_visit"]:
            continue
        if name in excluded:
            warnings.append(f"CONFLICTING_MUST_AND_AVOID:{name}")
            continue
        if name not in linked:
            proposal = _default_proposal(poi, attraction_preference)
            linked[name] = link_candidate_to_poi(proposal, poi, cluster_id=cluster_map.get(name))

    result: list[OptimizerCandidate] = []
    for name, candidate in linked.items():
        flags = _constraint_flags(name, all_names, constraints)
        merged_evidence = list(dict.fromkeys(candidate.evidence_constraint_ids + flags.pop("evidence_constraint_ids")))
        result.append(candidate.model_copy(update={**flags, "evidence_constraint_ids": merged_evidence}))
    result.sort(key=lambda item: (not item.must_visit, -_candidate_rank(item), item.poi_name))
    return result, warnings


def _candidate_rank(candidate: OptimizerCandidate) -> float:
    return (
        float(candidate.rating or 0) / 5
        + candidate.preference_match * 1.5
        + candidate.representativeness * 1.2
    )


def candidate_pool_fingerprint(candidates: Sequence[OptimizerCandidate | dict[str, Any]]) -> str:
    payload = [
        item.model_dump(mode="json") if isinstance(item, OptimizerCandidate) else item
        for item in candidates
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
