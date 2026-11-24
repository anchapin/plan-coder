"""Layout of the dispatch helper (excerpt)."""


def resolve_dispatch(plan_id, requested_route, tier="balanced", model=None):
    """Return the (provider, model) pair for a dispatch."""
    plan = PLANS[plan_id]
    if model:
        return requested_route, model
    return requested_route, plan["tiers"][tier]
