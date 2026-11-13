#!/usr/bin/env python3
"""Subscription-plan capacity model and route resolution.

A *plan* is the unit of capacity, not a vendor.  A plan owns one or more
routes (an API endpoint or a CLI harness), the models it may serve, and an
optional harness lock.  This module deliberately contains no vendor runner:
it validates plan data and resolves a requested route to a model.

The legacy ``providers`` configuration is represented as API-only plans by
:func:`plans_from_config`, so existing deployments keep their old provider
chain and tier ladder unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class DeploymentConfigError(ValueError):
    """The deployment configuration is structurally invalid."""


class PlanLockError(RuntimeError):
    """A dispatch requested a route forbidden by a plan's ``locked_to``."""


class RouteResolutionError(RuntimeError):
    """A requested route or model cannot be resolved for a plan."""


@dataclass(frozen=True)
class Route:
    """A route exposed by a subscription plan.

    API routes identify an endpoint.  CLI routes identify a harness (for
    example, an Anthropic-style Claude CLI), but deliberately do not carry
    vendor-specific runner logic in this reference slice.
    """

    kind: str
    endpoint: str | None = None
    harness: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "api":
            return f"api:{self.endpoint}"
        return f"cli:{self.harness}"

    def as_dict(self) -> dict[str, str]:
        if self.kind == "api":
            return {"kind": "api", "endpoint": self.endpoint or ""}
        return {"kind": "cli", "harness": self.harness or ""}


@dataclass(frozen=True)
class Plan:
    """A normalized subscription plan."""

    id: str
    vendor: str
    routes: tuple[Route, ...]
    models: Mapping[str, str]
    locked_to: tuple[Route, ...] = ()


@dataclass(frozen=True)
class Dispatch:
    """A successfully resolved (but not necessarily executed) dispatch."""

    plan_id: str
    vendor: str
    route: Route
    model: str
    tier: str


def _nonempty_string(value: Any, field: str, plan_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentConfigError(
            f"invalid plan {plan_id!r}: {field} must be a non-empty string")
    return value.strip()


def _parse_route(raw: Any, field: str, plan_id: str) -> Route:
    if not isinstance(raw, dict):
        raise DeploymentConfigError(
            f"invalid plan {plan_id!r}: {field} entries must be objects")
    kind = raw.get("kind")
    if kind not in ("api", "cli"):
        raise DeploymentConfigError(
            f"invalid plan {plan_id!r}: {field} route kind must be "
            "'api' or 'cli'")
    if kind == "api":
        endpoint = _nonempty_string(
            raw.get("endpoint"), f"{field} api endpoint", plan_id)
        return Route(kind="api", endpoint=endpoint)
    harness = _nonempty_string(
        raw.get("harness"), f"{field} cli harness", plan_id)
    return Route(kind="cli", harness=harness)


def _parse_models(raw: Any, plan_id: str) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise DeploymentConfigError(
            f"invalid plan {plan_id!r}: models must be a non-empty object "
            "mapping tier names to model ids")
    models: dict[str, str] = {}
    for tier, model in raw.items():
        tier_name = _nonempty_string(tier, "models tier", plan_id)
        model_id = _nonempty_string(model, f"model for tier {tier_name!r}", plan_id)
        if tier_name in models:
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: duplicate models tier {tier_name!r}")
        models[tier_name] = model_id
    return models


def validate_plans(config: Mapping[str, Any]) -> tuple[Plan, ...]:
    """Validate and normalize the optional top-level ``plans`` section.

    ``plans`` is a list of plan objects.  Every object declares its own
    ``id``.  A mapping is accepted as a convenience only when its entries
    also declare the same ``id`` explicitly, preserving the unambiguous
    "each plan declares id" schema.
    """
    if "plans" not in config:
        return ()
    raw_plans = config["plans"]
    records: list[dict[str, Any]] = []
    if isinstance(raw_plans, list):
        for index, raw in enumerate(raw_plans):
            if not isinstance(raw, dict):
                raise DeploymentConfigError(
                    f"invalid plans section: entry {index} must be an object")
            records.append(raw)
    elif isinstance(raw_plans, dict):
        for plan_id, raw in raw_plans.items():
            if not isinstance(raw, dict):
                raise DeploymentConfigError(
                    f"invalid plan {plan_id!r}: plan must be an object")
            records.append(raw)
    else:
        raise DeploymentConfigError(
            "invalid plans section: expected a list of plan objects")
    if not records:
        raise DeploymentConfigError(
            "invalid plans section: at least one plan is required")

    plans: list[Plan] = []
    seen_ids: set[str] = set()
    for raw in records:
        if "id" not in raw:
            raise DeploymentConfigError(
                "invalid plan: every plan must declare a non-empty id")
        plan_id = _nonempty_string(raw.get("id"), "id", "<unknown>")
        if plan_id in seen_ids:
            raise DeploymentConfigError(
                f"invalid plans section: duplicate plan id {plan_id!r}")
        seen_ids.add(plan_id)
        vendor = _nonempty_string(raw.get("vendor"), "vendor", plan_id)

        raw_routes = raw.get("routes")
        if not isinstance(raw_routes, list) or not raw_routes:
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: routes must be a non-empty list")
        routes = tuple(
            _parse_route(route, "routes", plan_id) for route in raw_routes)
        if len({route.key for route in routes}) != len(routes):
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: routes contains a duplicate route")

        models = _parse_models(raw.get("models"), plan_id)

        if "locked_to" not in raw:
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: locked_to is required "
                "(use [] for unrestricted)")
        raw_locks = raw["locked_to"]
        if not isinstance(raw_locks, list):
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: locked_to must be a list")
        locks = tuple(
            _parse_route(route, "locked_to", plan_id) for route in raw_locks)
        if len({lock.key for lock in locks}) != len(locks):
            raise DeploymentConfigError(
                f"invalid plan {plan_id!r}: locked_to contains a duplicate route")
        for lock in locks:
            if not any(lock.key == route.key for route in routes):
                raise DeploymentConfigError(
                    f"invalid plan {plan_id!r}: locked_to route {lock.key!r} "
                    "is not one of the plan's routes")

        plans.append(Plan(id=plan_id, vendor=vendor, routes=routes,
                          models=models, locked_to=locks))
    return tuple(plans)


def plans_from_config(config: Mapping[str, Any],
                      providers: Mapping[str, Mapping[str, Any]] | None = None
                      ) -> tuple[Plan, ...]:
    """Return explicit plans, or synthesize API plans from legacy providers.

    A legacy provider becomes one plan with one API route.  Its provider
    name is the plan id, its model map is the provider's tier map, and its
    chat endpoint is the route endpoint.
    """
    explicit = validate_plans(config)
    if explicit:
        return explicit
    result: list[Plan] = []
    for plan_id, provider in (providers or {}).items():
        endpoint = provider.get("chat_url")
        if not isinstance(endpoint, str) or not endpoint:
            raise DeploymentConfigError(
                f"invalid provider {plan_id!r}: chat_url must be a non-empty string")
        tiers = provider.get("tiers") or {}
        if not isinstance(tiers, dict):
            raise DeploymentConfigError(
                f"invalid provider {plan_id!r}: tiers must be an object")
        result.append(Plan(
            id=plan_id,
            vendor=plan_id,
            routes=(Route(kind="api", endpoint=endpoint),),
            models={str(tier): str(model) for tier, model in tiers.items()},
            locked_to=(),
        ))
    return tuple(result)


def _requested_route(requested: Route | Mapping[str, Any] | str,
                     plan: Plan) -> Route:
    if isinstance(requested, Route):
        matches = [route for route in plan.routes
                   if route.key == requested.key]
        if not matches:
            raise RouteResolutionError(
                f"plan {plan.id!r}: route {requested.key!r} is not available; "
                f"allowed routes: {_route_keys(plan.routes)}")
        return matches[0]
    if isinstance(requested, str):
        if requested not in ("api", "cli"):
            raise RouteResolutionError(
                f"plan {plan.id!r}: unsupported requested route {requested!r}")
        matches = [route for route in plan.routes if route.kind == requested]
    elif isinstance(requested, Mapping):
        kind = requested.get("kind")
        if kind == "api":
            endpoint = requested.get("endpoint")
            matches = [route for route in plan.routes
                       if route.kind == "api" and route.endpoint == endpoint]
        elif kind == "cli":
            harness = requested.get("harness")
            matches = [route for route in plan.routes
                       if route.kind == "cli" and route.harness == harness]
        else:
            raise RouteResolutionError(
                f"plan {plan.id!r}: unsupported requested route kind {kind!r}")
    else:
        raise RouteResolutionError(
            f"plan {plan.id!r}: requested route must be a route, object, or kind")
    if not matches:
        requested_key = requested.key if isinstance(requested, Route) else str(requested)
        raise RouteResolutionError(
            f"plan {plan.id!r}: route {requested_key!r} is not available; "
            f"allowed routes: {_route_keys(plan.routes)}")
    if len(matches) > 1:
        raise RouteResolutionError(
            f"plan {plan.id!r}: route kind {requested!r} is ambiguous; "
            "specify endpoint or harness")
    return matches[0]


def _route_keys(routes: Sequence[Route]) -> str:
    return ", ".join(route.key for route in routes) or "(none)"


def resolve_dispatch(plan: Plan,
                      requested: Route | Mapping[str, Any] | str,
                      tier: str = "balanced",
                      model: str | None = None) -> Dispatch:
    """Resolve one dispatch, enforcing the plan's harness lock.

    A non-empty ``locked_to`` is an allow-list.  A request outside that
    allow-list raises :class:`PlanLockError`; there is intentionally no
    fallback to another route or plan.
    """
    # Check the lock before route availability.  For a locked plan, a
    # request for any other route is a lock violation even when that route
    # was never declared; the error must explain the lock, not merely say
    # that the requested route is absent.
    if plan.locked_to:
        requested_key: str | None = None
        if isinstance(requested, Route):
            requested_key = requested.key
        elif isinstance(requested, str):
            kind_matches = [lock for lock in plan.locked_to
                            if lock.kind == requested]
            if len(kind_matches) == 1:
                requested_key = kind_matches[0].key
        elif isinstance(requested, Mapping):
            kind = requested.get("kind")
            if kind == "api":
                requested_key = f"api:{requested.get('endpoint')}"
            elif kind == "cli":
                requested_key = f"cli:{requested.get('harness')}"
        allowed_keys = {lock.key for lock in plan.locked_to}
        if requested_key is None or requested_key not in allowed_keys:
            shown = requested_key if requested_key is not None else str(requested)
            raise PlanLockError(
                f"plan {plan.id!r} is locked and does not allow requested route "
                f"{shown!r}; allowed routes: {_route_keys(plan.locked_to)} "
                f"(locked_to: {_route_keys(plan.locked_to)})")

    route = _requested_route(requested, plan)
    if model is None:
        if tier not in plan.models:
            raise RouteResolutionError(
                f"plan {plan.id!r}: tier {tier!r} is not available; "
                f"available tiers: {', '.join(plan.models)}")
        model = plan.models[tier]
    elif model not in plan.models.values():
        raise RouteResolutionError(
            f"plan {plan.id!r}: model {model!r} is not available; "
            f"available models: {', '.join(plan.models.values())}")
    return Dispatch(plan_id=plan.id, vendor=plan.vendor, route=route,
                    model=model, tier=tier)


def cli_invocation(route: Route, model: str) -> list[str]:
    """Describe the generic CLI invocation for a resolved route.

    This is intentionally a reference interface, not a vendor runner.  The
    harness name is the command; a caller may hand this list to its own
    runner, while ``plan_cli_stub.py`` records it without invoking anything.
    """
    if route.kind != "cli" or not route.harness:
        raise RouteResolutionError(
            f"CLI invocation requires a cli route, got {route.key!r}")
    if not isinstance(model, str) or not model.strip():
        raise RouteResolutionError("CLI invocation requires a model id")
    return [route.harness, "--model", model]
