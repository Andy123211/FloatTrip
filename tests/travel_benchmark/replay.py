"""Replay only the production optimizer using saved inputs, with zero provider calls."""
from __future__ import annotations

from .common import digest, now, provenance


async def replay_optimizer(record):
    from app.planning.schemas import TravelPlanState
    from app.planning.nodes import optimize_attractions_node
    state=TravelPlanState.model_validate(record['state'])
    result=await optimize_attractions_node(state)
    def selection(route):
        return {s['name'] for day in route for s in day.get('spots',[])}
    old=selection(state.route)
    new=selection(result.get('route',[]))
    return {'created_at':now(),'replay_provenance':provenance(),'case_id':record['case_id'],'candidate_fingerprint':digest(state.candidate_pool),
        'source_trial':{'mode':record['mode'],'trial':record['trial']},
        'same_selected_set':old==new,'same_route':state.route==result.get('route'),
        'original_only':sorted(old-new),'replay_only':sorted(new-old),'result':result,
        'note':'Frozen final pool, dates, weather and constraints; live services and candidate model were not rerun. A wall-clock solver limit can still change search termination.'}
