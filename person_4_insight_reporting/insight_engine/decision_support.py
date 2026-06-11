"""
Decision support layer — structured actions, evidence, and triage for incident commanders.

Additive to the existing alert contract: enriches alerts and summary without changing
legacy Person 5 fields (zone_id, insight_type, message, confidence).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from narration import _build_context, _zone_label

if TYPE_CHECKING:
    from alert_state import Alert


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_alert(alert: "Alert", signals: dict, all_alerts: list["Alert"]) -> dict:
    """Returns the standard alert dict plus decision-support fields."""
    from alert_state import _alert_to_dict

    base = _alert_to_dict(alert)
    context = _build_context(alert.insight_type, alert.zone_id, signals, alert)
    evidence = build_evidence(alert, context, signals)
    actions = build_actions(alert, context, signals)
    dependencies = build_dependencies(alert, all_alerts, signals)
    escalation = build_escalation(alert, context)

    enriched = {
        **base,
        "because": build_because(evidence, alert.insight_type, alert.severity),
        "evidence": evidence,
        "actions": actions,
        "dependencies": dependencies,
    }
    if escalation:
        enriched.update(escalation)
    return enriched


def build_summary_decisions(
    alerts: list["Alert"],
    enriched_alerts: list[dict],
) -> dict:
    """Triage buckets and cross-alert priority actions for the global summary."""
    triage = build_triage(alerts)
    priority_actions = _collect_priority_actions(triage, enriched_alerts)
    action_headline = _action_headline(triage, alerts, priority_actions)

    extras: dict = {"triage": triage}
    if priority_actions:
        extras["priority_actions"] = priority_actions
    if action_headline:
        extras["action_headline"] = action_headline
    return extras


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def build_evidence(alert: "Alert", context: dict, signals: dict) -> dict:
    duration_s = signals.get("flow_duration_s", 300) or 300
    inbound = context.get("inbound", 0)
    outbound = context.get("outbound", 0)

    evidence: dict = {
        "inbound_count": inbound,
        "outbound_count": outbound,
        "inbound_rate_per_min": round(inbound / duration_s * 60, 1) if duration_s else 0,
        "outbound_rate_per_min": round(outbound / duration_s * 60, 1) if duration_s else 0,
        "accumulation_ratio": context.get("accumulation_ratio", 0),
        "capacity_pct_estimate": _capacity_pct(context.get("accumulation_ratio", 0)),
        "dwell_seconds": context.get("dwell_s", 0),
        "traffic_growth_x": context.get("traffic_growth_x"),
        "windows_observed": context.get("window_count", 0),
        "cycle_count": context.get("cycle_count", 1),
    }

    sources = context.get("convergence_sources", [])
    if sources:
        evidence["convergence_sources"] = sources
        evidence["convergence_source_count"] = context.get(
            "convergence_source_count", len(sources)
        )

    if context.get("from_zone"):
        evidence["from_zone"] = context["from_zone"]
        evidence["transition_count"] = context.get("transition_count", 0)

    if context.get("presence_ratio") is not None:
        evidence["presence_ratio"] = context["presence_ratio"]

    return evidence


def build_because(evidence: dict, insight_type: str, severity: str) -> str:
    inbound = evidence.get("inbound_count", 0)
    outbound = evidence.get("outbound_count", 0)
    growth = evidence.get("traffic_growth_x")
    dwell = evidence.get("dwell_seconds", 0)
    sources = evidence.get("convergence_sources", [])
    capacity = evidence.get("capacity_pct_estimate", 0)

    if insight_type == "congestion_forecast":
        parts = []
        if growth and growth > 1.2:
            parts.append(f"traffic up {growth:.1f}×")
        if inbound and outbound and inbound > outbound * 2:
            parts.append(f"inbound {inbound} vs outbound {outbound}")
        if capacity:
            parts.append(f"~{capacity}% of estimated capacity")
        return "; ".join(parts) if parts else "Sustained upward traffic trend"

    if insight_type == "bottleneck_risk":
        if sources:
            labels = ", ".join(_zone_label(s) for s in sources[:3])
            return f"{len(sources)} approach paths ({labels}) converging on one sector"
        if inbound and outbound:
            return f"Inbound {inbound} vs outbound {outbound} — flow stacking at choke point"
        return "Multiple feeders routing into one sector"

    if insight_type == "high_dwell_zone":
        if dwell:
            return f"Average dwell {dwell}s — movement slowing in this sector"
        return "Dwell time well above building norm"

    if insight_type == "unexpected_transition":
        from_z = evidence.get("from_zone")
        count = evidence.get("transition_count", 0)
        if from_z:
            return f"New route {_zone_label(from_z)} → destination, used {count} time(s) this session"
        return "Route not seen earlier in this session"

    if insight_type == "anomaly":
        if evidence.get("presence_ratio") is not None:
            return "Sector quiet while upstream feeders remain active"
        return "Activity pattern shifted from earlier observations"

    if severity == "resolving":
        return "Conditions improving across recent windows"
    return "Unusual conditions detected in this sector"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def build_actions(alert: "Alert", context: dict, signals: dict) -> list[dict]:
    itype = alert.insight_type
    severity = alert.severity
    zone_id = alert.zone_id
    label = _zone_label(zone_id)
    nodes = context.get("nodes", signals.get("nodes", []))
    sources = context.get("convergence_sources", [])
    confidence = round(alert.confidence, 3)

    if severity == "resolving":
        return [_action(
            verb="continue_monitoring",
            label=f"Continue monitoring {label} — situation stabilizing",
            zone_id=zone_id,
            urgency="when_convenient",
            rationale="Conditions are improving; confirm trend holds",
            confidence=confidence,
            priority=3,
        )]

    if itype == "congestion_forecast":
        return _congestion_actions(zone_id, label, severity, context, nodes, confidence)

    if itype == "bottleneck_risk":
        return _bottleneck_actions(zone_id, label, severity, context, nodes, sources, confidence)

    if itype == "high_dwell_zone":
        return _dwell_actions(zone_id, label, severity, context, confidence)

    if itype == "unexpected_transition":
        return _unexpected_actions(zone_id, label, severity, context, confidence)

    if itype == "anomaly":
        return _anomaly_actions(zone_id, label, severity, context, confidence)

    return [_action(
        verb="monitor",
        label=f"Monitor {label} for further development",
        zone_id=zone_id,
        urgency="watch",
        rationale=build_because(build_evidence(alert, context, signals), itype, severity),
        confidence=confidence,
        priority=2,
    )]


def _congestion_actions(
    zone_id: str,
    label: str,
    severity: str,
    context: dict,
    nodes: list,
    confidence: float,
) -> list[dict]:
    alternates = _alternate_zones(zone_id, nodes, context.get("convergence_sources", []))
    capacity = _capacity_pct(context.get("accumulation_ratio", 0))
    growth = context.get("traffic_growth_x")
    rationale_parts = []
    if growth and growth > 1.2:
        rationale_parts.append(f"traffic up {growth:.1f}×")
    if capacity:
        rationale_parts.append(f"~{capacity}% of estimated capacity")
    rationale = "; ".join(rationale_parts) or "Sustained inbound pressure"

    actions: list[dict] = []

    if severity == "critical":
        actions.append(_action(
            verb="immediate_intervention",
            label=f"Immediate intervention at {label} — sector may become impassable",
            zone_id=zone_id,
            urgency="immediate",
            rationale=rationale,
            if_ignored="Sector likely impassable within 1–2 windows without relief",
            confidence=confidence,
            priority=1,
        ))

    if alternates and severity in ("warning", "critical"):
        target = alternates[0]
        actions.append(_action(
            verb="divert_flow",
            label=f"Divert inbound traffic through {_zone_label(target)}",
            zone_id=zone_id,
            target_zones=[target],
            affected_zones=[zone_id] + context.get("convergence_sources", [])[:3],
            urgency="immediate" if severity == "critical" else "soon",
            rationale=rationale,
            if_ignored=f"{label} may reach capacity before outflow recovers",
            confidence=confidence,
            priority=1 if severity == "critical" else 2,
        ))
    elif severity in ("warning", "critical"):
        actions.append(_action(
            verb="restrict_entry",
            label=f"Slow or restrict entry into {label}",
            zone_id=zone_id,
            urgency="soon" if severity == "warning" else "immediate",
            rationale=rationale,
            if_ignored="Queue depth will continue rising",
            confidence=confidence,
            priority=2,
        ))

    if severity == "detecting":
        actions.append(_action(
            verb="monitor",
            label=f"Monitor {label} — congestion pattern emerging",
            zone_id=zone_id,
            urgency="watch",
            rationale=rationale,
            confidence=confidence,
            priority=3,
        ))

    return actions or [_action(
        verb="monitor",
        label=f"Monitor {label}",
        zone_id=zone_id,
        urgency="watch",
        rationale=rationale,
        confidence=confidence,
        priority=3,
    )]


def _bottleneck_actions(
    zone_id: str,
    label: str,
    severity: str,
    context: dict,
    nodes: list,
    sources: list,
    confidence: float,
) -> list[dict]:
    alternates = _alternate_zones(zone_id, nodes, sources)
    source_labels = ", ".join(_zone_label(s) for s in sources[:3]) if sources else "multiple feeders"
    rationale = (
        f"{len(sources)} approach paths ({source_labels}) converging on {label}"
        if sources else f"Inbound pressure building at {label}"
    )
    actions: list[dict] = []

    if severity == "critical":
        actions.append(_action(
            verb="immediate_intervention",
            label=f"Immediate intervention at {label} — sector may become impassable",
            zone_id=zone_id,
            urgency="immediate",
            rationale=rationale,
            if_ignored="Convergence point may block movement through the building",
            confidence=confidence,
            priority=1,
            owner_role="sector_officer",
        ))

    if alternates and severity in ("warning", "critical"):
        target = alternates[0]
        actions.append(_action(
            verb="divert_flow",
            label=f"Divert flow from {source_labels} through {_zone_label(target)} instead of {label}",
            zone_id=zone_id,
            target_zones=[target],
            affected_zones=[zone_id] + sources[:3],
            urgency="immediate" if severity == "critical" else "soon",
            rationale=rationale,
            if_ignored=f"{label} overwhelmed even if individual feeders look manageable",
            confidence=confidence,
            priority=1 if severity == "critical" else 2,
            owner_role="entry_control",
        ))
    elif severity in ("warning", "critical"):
        actions.append(_action(
            verb="open_alternate_route",
            label=f"Open an alternative route around {label}",
            zone_id=zone_id,
            urgency="soon",
            rationale=rationale,
            confidence=confidence,
            priority=2,
        ))

    if severity == "detecting":
        actions.append(_action(
            verb="monitor",
            label=f"Watch convergence pressure at {label}",
            zone_id=zone_id,
            urgency="watch",
            rationale=rationale,
            confidence=confidence,
            priority=3,
        ))

    return actions or [_action(
        verb="monitor",
        label=f"Monitor {label}",
        zone_id=zone_id,
        urgency="watch",
        rationale=rationale,
        confidence=confidence,
        priority=3,
    )]


def _dwell_actions(
    zone_id: str,
    label: str,
    severity: str,
    context: dict,
    confidence: float,
) -> list[dict]:
    dwell = context.get("dwell_s", 0)
    rationale = f"Average dwell {dwell}s — well above norm" if dwell else "Extended stays detected"

    if severity in ("warning", "critical"):
        return [_action(
            verb="visual_inspection",
            label=f"Send team for visual inspection of {label}",
            zone_id=zone_id,
            urgency="immediate" if severity == "critical" else "soon",
            rationale=rationale,
            if_ignored="Obstruction or staging may spread to adjacent sectors",
            confidence=confidence,
            priority=1 if severity == "critical" else 2,
            owner_role="recon_team",
        )]

    return [_action(
        verb="monitor",
        label=f"Monitor dwell times at {label}",
        zone_id=zone_id,
        urgency="watch",
        rationale=rationale,
        confidence=confidence,
        priority=3,
    )]


def _unexpected_actions(
    zone_id: str,
    label: str,
    severity: str,
    context: dict,
    confidence: float,
) -> list[dict]:
    from_zone = context.get("from_zone", "unknown")
    from_label = _zone_label(from_zone)
    count = context.get("transition_count", 0)
    rationale = f"New path {from_label} → {label}, used {count} time(s) this session"

    urgency = "soon" if severity in ("warning", "critical") else "watch"
    return [_action(
        verb="investigate_route",
        label=f"Investigate new route {from_label} → {label}",
        zone_id=zone_id,
        affected_zones=[from_zone, zone_id],
        urgency=urgency,
        rationale=rationale,
        if_ignored="May indicate evacuation, breach, or access to an unused area",
        confidence=confidence,
        priority=1 if severity == "critical" else 2,
        owner_role="recon_team",
    )]


def _anomaly_actions(
    zone_id: str,
    label: str,
    severity: str,
    context: dict,
    confidence: float,
) -> list[dict]:
    if context.get("presence_ratio") is not None:
        rationale = f"{label} quiet while upstream feeders remain active"
        return [_action(
            verb="verify_access",
            label=f"Verify access and conditions at {label}",
            zone_id=zone_id,
            urgency="soon" if severity in ("warning", "critical") else "watch",
            rationale=rationale,
            if_ignored="Possible blockage, access loss, or evacuation",
            confidence=confidence,
            priority=2,
            owner_role="recon_team",
        )]

    return [_action(
        verb="verify_conditions",
        label=f"Confirm whether shift at {label} is operational or sensor artifact",
        zone_id=zone_id,
        urgency="watch",
        rationale="Activity pattern differs from earlier observations",
        confidence=confidence,
        priority=3,
    )]


def _action(
    *,
    verb: str,
    label: str,
    zone_id: str,
    urgency: str,
    rationale: str,
    confidence: float,
    priority: int,
    target_zones: list[str] | None = None,
    affected_zones: list[str] | None = None,
    if_ignored: str | None = None,
    owner_role: str | None = None,
) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower())[:40].strip("_")
    action_id = f"{verb}_{zone_id}_{slug}" if slug else f"{verb}_{zone_id}"

    action: dict = {
        "id": action_id,
        "priority": priority,
        "verb": verb,
        "label": label,
        "zone_id": zone_id,
        "urgency": urgency,
        "rationale": rationale,
        "confidence": confidence,
    }
    if target_zones:
        action["target_zones"] = target_zones
    if affected_zones:
        action["affected_zones"] = affected_zones
    if if_ignored:
        action["if_ignored"] = if_ignored
    if owner_role:
        action["owner_role"] = owner_role
    return action


# ---------------------------------------------------------------------------
# Dependencies & escalation
# ---------------------------------------------------------------------------

def build_dependencies(
    alert: "Alert",
    all_alerts: list["Alert"],
    signals: dict,
) -> list[dict]:
    deps: list[dict] = []
    zone = alert.zone_id
    by_zone = {a.zone_id: a for a in all_alerts}

    for cas in signals.get("cascades", []):
        if cas.get("to_zone") == zone:
            upstream = cas.get("from_zone")
            upstream_alert = by_zone.get(upstream)
            if upstream_alert:
                deps.append({
                    "type": "cascade_risk",
                    "upstream_alert": upstream_alert.id,
                    "upstream_zone": upstream,
                    "downstream_zone": zone,
                    "implication": (
                        f"Address {_zone_label(upstream)} before "
                        f"{_zone_label(zone)} becomes critical"
                    ),
                })

        if cas.get("from_zone") == zone:
            downstream = cas.get("to_zone")
            downstream_alert = by_zone.get(downstream)
            if downstream_alert:
                deps.append({
                    "type": "cascade_downstream",
                    "downstream_alert": downstream_alert.id,
                    "downstream_zone": downstream,
                    "implication": (
                        f"If {_zone_label(zone)} tips critical, "
                        f"{_zone_label(downstream)} may follow within 1–2 windows"
                    ),
                })

    for conv in signals.get("convergence", []):
        if conv.get("zone_id") == zone and len(conv.get("sources", [])) >= 2:
            sources = conv["sources"]
            deps.append({
                "type": "convergence",
                "source_zones": sources,
                "implication": (
                    f"{len(sources)} sectors feeding {_zone_label(zone)} — "
                    f"choke point may overwhelm before feeders show stress"
                ),
            })
            break

    return deps


def build_escalation(alert: "Alert", context: dict) -> dict | None:
    if alert.severity in ("critical", "resolving", "resolved"):
        return None

    growth = context.get("traffic_growth_x")
    windows = context.get("window_count", 0)
    capacity = _capacity_pct(context.get("accumulation_ratio", 0))

    if alert.severity == "warning" and (growth and growth >= 2.0 or capacity >= 60):
        estimate = "~90 seconds" if growth and growth >= 3.0 else "~2–3 windows"
        return {
            "time_to_critical_estimate": estimate,
            "escalation_trigger": (
                "If inbound rate does not drop ~30% over the next 2 windows"
            ),
            "if_unaddressed": (
                f"{_zone_label(alert.zone_id)} likely reaches critical without intervention"
            ),
        }

    if alert.severity == "detecting" and growth and growth >= 1.5 and windows >= 2:
        return {
            "time_to_critical_estimate": "~3–5 windows at current growth",
            "escalation_trigger": "If growth continues above 1.5× per window",
            "if_unaddressed": "Pattern may escalate to warning",
        }

    return None


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

def build_triage(alerts: list["Alert"]) -> dict:
    act_now: list[str] = []
    watch: list[str] = []
    improving: list[str] = []

    for alert in alerts:
        if alert.severity == "resolving":
            improving.append(alert.id)
        elif alert.severity == "critical":
            act_now.append(alert.id)
        elif alert.severity == "warning":
            act_now.append(alert.id)
        else:
            watch.append(alert.id)

    return {
        "act_now": act_now,
        "watch": watch,
        "improving": improving,
    }


def _collect_priority_actions(triage: dict, enriched_alerts: list[dict]) -> list[dict]:
    by_id = {a["id"]: a for a in enriched_alerts}
    seen: set[str] = set()
    collected: list[dict] = []

    for alert_id in triage.get("act_now", []):
        alert_dict = by_id.get(alert_id)
        if not alert_dict:
            continue
        for action in alert_dict.get("actions", []):
            aid = action.get("id")
            if aid in seen:
                continue
            seen.add(aid)
            collected.append({**action, "alert_id": alert_id})

    collected.sort(key=lambda a: (a.get("priority", 99), -a.get("confidence", 0)))
    return collected[:5]


def _action_headline(
    triage: dict,
    alerts: list["Alert"],
    priority_actions: list[dict],
) -> str | None:
    act_count = len(triage.get("act_now", []))
    if act_count == 0:
        if triage.get("watch"):
            return f"{len(triage['watch'])} situation(s) to watch — no immediate action required"
        return None

    critical = sum(1 for a in alerts if a.severity == "critical")
    top = priority_actions[0]["label"] if priority_actions else None

    if critical and top:
        return f"{critical} critical — {top}"
    if critical:
        return f"{critical} critical situation(s) require immediate action"
    if top:
        return f"{act_count} developing — {top}"
    return f"{act_count} situation(s) require attention"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capacity_pct(acc_ratio: float) -> int:
    if acc_ratio <= 0:
        return 0
    return min(95, max(5, int(acc_ratio / 5.0 * 100)))


def _alternate_zones(zone_id: str, nodes: list, exclude: list[str]) -> list[str]:
    exclude_set = set(exclude or []) | {zone_id}
    return [n for n in nodes if n not in exclude_set]
