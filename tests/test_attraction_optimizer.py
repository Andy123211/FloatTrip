from __future__ import annotations

import itertools
from datetime import date

import pytest
from pydantic import ValidationError

from app.planning.candidate_builder import build_authoritative_candidates
from app.planning.optimizer import (
    AttractionSubsetOptimizer,
    OptimizationFailure,
    _earliest_schedule,
    daily_bounds_from_constraints,
    meal_coverage_for_visit,
    pace_range,
    validate_solution,
)
from app.planning.scoring import evaluate_itinerary, load_scoring_profile
from app.planning.helpers import _round_robin_spots
from app.planning.schemas import CandidateAttraction, OptimizerCandidate, SolverDiagnostics
from app.planning.semantics import infer_meal_scene
from app.planning.shadow_profiles import compare_shadow_routes
from app.providers.amap.poi import poi_to_spot


def candidate(
    name: str,
    index: int,
    *,
    preference: float = 0.5,
    representative: float = 0.5,
    scene: str = "none",
    fixed_start: int | None = None,
    must_visit: bool = False,
) -> OptimizerCandidate:
    return OptimizerCandidate(
        poi_name=name,
        duration_min=90,
        preference_match=preference,
        representativeness=representative,
        preferred_period="any",
        meal_scene=scene,
        semantic_tags=[],
        evidence_constraint_ids=[],
        location={"lng": 118.7 + index * 0.01, "lat": 32.0},
        rating=4.5,
        poi_type="风景名胜",
        typecode="110000",
        category="other",
        cluster_id=0,
        semantic_source="rule" if scene != "none" else "llm",
        semantic_evidence=[],
        fixed_start_min=fixed_start,
        must_visit=must_visit,
    )


def test_amap_type_fields_are_preserved_and_rule_semantics_win():
    spot = poi_to_spot({
        "id": "p1",
        "name": "南门夜市",
        "location": "118.78,32.04",
        "type": "餐饮服务;特色商业街",
        "typecode": "050000",
        "biz_ext": {"rating": "4.7"},
    })
    assert spot["type"] == "餐饮服务;特色商业街"
    assert spot["typecode"] == "050000"
    assert infer_meal_scene(spot).meal_scene == "dinner"
    linked, _ = build_authoritative_candidates(
        [CandidateAttraction(poi_name="南门夜市", meal_scene="lunch")],
        [spot],
        days=1,
    )
    assert linked[0].meal_scene == "dinner"
    assert linked[0].semantic_source == "rule"
    assert linked[0].duration_min == 90


def test_food_street_search_family_is_not_starved_by_popular_poi_results():
    popular = [
        {"name": f"普通景点{i}", "location": f"118.{i:02d},32.0", "biz_ext": {"rating": "4.8"}}
        for i in range(20)
    ]
    food = [{"name": "老门东小吃街", "location": "118.79,32.03", "type": "餐饮服务"}]
    selected = _round_robin_spots([popular, [], [], food, []], max_spots=4)
    assert "老门东小吃街" in {item["name"] for item in selected}


def test_sightseeing_trip_deprioritizes_food_streets_but_food_focus_keeps_them():
    pois = [
        {"name": "南京博物院", "location": {"lng": 118.80, "lat": 32.04}, "rating": 4.8},
        {"name": "夫子庙", "location": {"lng": 118.79, "lat": 32.02}, "rating": 4.7},
        {"name": "老门东小吃街", "location": {"lng": 118.79, "lat": 32.03}, "type": "餐饮服务", "rating": 4.7},
        {"name": "狮子桥美食街", "location": {"lng": 118.78, "lat": 32.05}, "type": "餐饮服务", "rating": 4.6},
    ]
    proposals = [CandidateAttraction(poi_name=poi["name"]) for poi in pois]
    sightseeing, warnings = build_authoritative_candidates(proposals, pois, days=2)
    assert sum(item.category == "food_street" for item in sightseeing) == 1
    assert any(item.startswith("FOOD_STREET_DEPRIORITIZED") for item in warnings)
    food_focused, _ = build_authoritative_candidates(proposals, pois, days=2, food_focused=True)
    assert sum(item.category == "food_street" for item in food_focused) == 2


def test_unknown_llm_meal_scene_is_rejected():
    with pytest.raises(ValidationError):
        CandidateAttraction(poi_name="X", meal_scene="brunch")


@pytest.mark.parametrize(
    ("scene", "start", "end", "expected"),
    [
        ("lunch", 690, 780, "lunch"),
        ("dinner", 1080, 1170, "dinner"),
        ("either", 1080, 1170, "dinner"),
        ("either", 690, 1170, "lunch"),  # same visit covers at most one
        ("lunch", 780, 810, None),  # only 30 minutes of overlap
        ("none", 690, 780, None),
    ],
)
def test_meal_coverage_windows(scene, start, end, expected):
    assert meal_coverage_for_visit(scene, start, end) == expected


def test_cp_sat_matches_exhaustive_global_optimum_on_small_pool():
    candidates = [
        candidate("A", 0, preference=1.0, representative=0.9),
        candidate("B", 1, preference=0.8, representative=0.8),
        candidate("C", 2, preference=0.3, representative=0.4),
        candidate("D", 3, preference=0.1, representative=0.2),
    ]
    optimizer = AttractionSubsetOptimizer(max_time_seconds=1)
    result = optimizer.solve(candidates, days=1, habit_preference="慢节奏", max_per_day=2)
    assert result.diagnostics.status == "OPTIMAL"

    profile = load_scoring_profile()
    candidate_map = {item.poi_name: item.model_dump() for item in candidates}
    best = float("-inf")
    best_sets = set()
    for subset in itertools.combinations(candidates, 2):
        for order in itertools.permutations(subset):
            schedule = _earliest_schedule([item.model_dump() for item in order])
            assert schedule
            route = [{
                "day": 1,
                "spots": [
                    {
                        "name": item.poi_name,
                        "start_min": slot[0],
                        "end_min": slot[1],
                        "meal_coverage": None,
                    }
                    for item, slot in zip(order, schedule)
                ],
            }]
            score, _ = evaluate_itinerary(route, candidate_map, profile, daily_target=2)
            chosen = frozenset(item.poi_name for item in order)
            if score > best + 1e-8:
                best, best_sets = score, {chosen}
            elif abs(score - best) <= 1e-8:
                best_sets.add(chosen)
    selected = frozenset(spot["name"] for spot in result.route[0]["spots"])
    assert selected in best_sets
    assert result.diagnostics.objective == pytest.approx(best)


def test_meal_soft_goal_yields_to_fixed_appointment_hard_constraint():
    food = candidate("固定上午夜市", 0, scene="dinner", fixed_start=600, must_visit=True)
    other = candidate("博物馆", 1, must_visit=True)
    result = AttractionSubsetOptimizer().solve(
        [food, other], days=1, habit_preference="慢节奏", max_per_day=2
    )
    food_spot = next(spot for spot in result.route[0]["spots"] if spot["name"] == food.poi_name)
    assert food_spot["start_time"] == "10:00"
    assert food_spot["meal_coverage"] is None
    miss = next(item for item in result.score_breakdown if item["component"] == "meal_scene_miss")
    assert miss["raw_value"] == 1


def test_system_daily_min_relaxes_but_user_max_does_not():
    result = AttractionSubsetOptimizer().solve(
        [candidate("A", 0), candidate("B", 1), candidate("C", 2)],
        days=2,
        habit_preference="慢节奏",
        max_per_day=2,
    )
    assert sorted(len(day["spots"]) for day in result.route) == [1, 2]
    assert "RELAXED_DAILY_MIN" in result.diagnostics.relaxed_constraints
    assert all(len(day["spots"]) <= 2 for day in result.route)


def test_zero_solver_budget_uses_deterministic_fallback():
    result = AttractionSubsetOptimizer(max_time_seconds=0).solve(
        [candidate("A", 0), candidate("B", 1), candidate("C", 2)],
        days=1,
        habit_preference="慢节奏",
        max_per_day=2,
    )
    assert result.diagnostics.status == "FALLBACK"
    assert result.diagnostics.fallback_used is True


@pytest.mark.parametrize("solver_seconds", [0, 1])
def test_solver_and_fallback_generate_daily_theme_names(solver_seconds):
    result = AttractionSubsetOptimizer(max_time_seconds=solver_seconds).solve(
        [candidate("上海动物园", 0, must_visit=True), candidate("七宝老街", 1, must_visit=True)],
        days=1,
        habit_preference="慢节奏",
        max_per_day=2,
    )
    assert result.route[0]["theme"] == "自然寻趣·老街慢游"
    assert {spot["name"] for spot in result.route[0]["spots"]} == {"上海动物园", "七宝老街"}


def test_profile_weight_change_only_changes_soft_selection():
    food = candidate("夜市", 0, preference=0.0, representative=0.5, scene="dinner")
    museum = candidate("博物馆", 1, preference=1.0, representative=0.5)
    default_optimizer = AttractionSubsetOptimizer()
    default = default_optimizer.solve(
        [food, museum], days=1, habit_preference="慢节奏", max_per_day=1
    )
    assert default.route[0]["spots"][0]["name"] == "博物馆"

    boosted_optimizer = AttractionSubsetOptimizer()
    boosted_rewards = boosted_optimizer.profile.rewards.model_copy(
        update={"meal_scene_match": 3.0}
    )
    boosted_optimizer.profile = boosted_optimizer.profile.model_copy(
        update={"version": "meal-boost-test", "rewards": boosted_rewards}
    )
    boosted = boosted_optimizer.solve(
        [food, museum], days=1, habit_preference="慢节奏", max_per_day=1
    )
    assert boosted.route[0]["spots"][0]["name"] == "夜市"
    assert boosted.route[0]["spots"][0]["meal_coverage"] == "dinner"


def test_first_last_precedence_and_weekday_closure_are_hard_constraints():
    first = candidate("首站", 0, must_visit=True).model_copy(update={"must_be_first": True})
    middle = candidate("中间站", 1, must_visit=True).model_copy(
        update={"before_poi_names": ["末站"]}
    )
    last = candidate("末站", 2, must_visit=True).model_copy(update={"must_be_last": True})
    ordered = AttractionSubsetOptimizer().solve(
        [last, middle, first], days=1, habit_preference="正常", max_per_day=3
    )
    assert [spot["name"] for spot in ordered.route[0]["spots"]] == ["首站", "中间站", "末站"]
    assert ordered.quality_report.passed

    closed_monday = candidate("周一闭馆馆", 3, must_visit=True).model_copy(
        update={"open_time": "周一闭馆；周二至周日09:00-18:00"}
    )
    always_open = candidate("每日开放馆", 4, must_visit=True)
    scheduled = AttractionSubsetOptimizer().solve(
        [closed_monday, always_open],
        days=2,
        habit_preference="慢节奏",
        max_per_day=1,
        travel_start_date=date(2026, 8, 17),  # Monday
    )
    assert scheduled.route[0]["spots"][0]["name"] == "每日开放馆"
    assert scheduled.route[1]["spots"][0]["name"] == "周一闭馆馆"


def test_explicit_daily_count_bounds_are_hard_and_never_relaxed():
    constraints = [{
        "id": "count", "polarity": "require", "category": "travel_pace",
        "value_text": "每天至少两个景点，最多三个景点",
    }]
    assert daily_bounds_from_constraints(constraints) == (2, 3)
    with pytest.raises(OptimizationFailure, match="no feasible route"):
        AttractionSubsetOptimizer().solve(
            [candidate("A", 0), candidate("B", 1), candidate("C", 2)],
            days=2,
            habit_preference="慢节奏",
            max_per_day=5,
            user_daily_min=2,
            user_daily_max=3,
        )


def test_route_detour_ratio_is_diagnostic_not_a_quality_gate_failure():
    candidates = [candidate("A", 0), candidate("B", 1), candidate("C", 2)]
    route = [{
        "day": 1,
        "spots": [
            {"name": "A", "start_min": 540, "end_min": 630, "meal_coverage": None},
            {"name": "C", "start_min": 650, "end_min": 740, "meal_coverage": None},
            {"name": "B", "start_min": 760, "end_min": 850, "meal_coverage": None},
        ],
    }]
    report = validate_solution(
        route,
        candidates,
        SolverDiagnostics(
            status="OPTIMAL", objective=None, best_bound=None, gap=0,
            elapsed_ms=0, scoring_profile="balanced-v1", random_seed=1,
        ),
        load_scoring_profile(),
        pace_range("慢节奏", 3),
    )
    assert report.daily_order_ratios[1] > 1.05
    assert report.passed
    assert not any(item.code == "ORDER_DETOUR" for item in report.violations)


def test_shadow_comparison_uses_same_candidates_and_reports_route_metrics():
    candidates = {
        item.poi_name: item.model_dump()
        for item in (candidate("A", 0), candidate("B", 1), candidate("C", 2))
    }
    active = [{"day": 1, "spots": [
        {"name": "A", "meal_coverage": None},
        {"name": "B", "meal_coverage": "lunch"},
    ]}]
    shadow = [{"day": 1, "spots": [
        {"name": "A", "meal_coverage": None},
        {"name": "C", "meal_coverage": None},
    ]}]
    comparison = compare_shadow_routes(active, shadow, candidates)
    assert comparison["selected_jaccard"] == pytest.approx(1 / 3)
    assert comparison["active_only"] == ["B"]
    assert comparison["shadow_only"] == ["C"]
    assert comparison["active_meal_coverage"] == [(1, "B", "lunch")]
