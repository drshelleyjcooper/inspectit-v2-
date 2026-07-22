"""Controlled vocabulary and validation for the product-analytics event
stream (the `events` table). Metric definitions built on top of this land
in docs/analytics-api.md in Stage 2 — this module only owns "is this event
shaped acceptably," not what it means.

Privacy posture: events carry ids and coarse feature names, never
inspection contents, plate numbers, addresses, or other customer PII. A
platform admin reading aggregate analytics is fine; this table must never
become a way to read what a specific customer inspected. That's enforced
on the client (the emitter in web/inspectit-app.html never reads record
fields into event_props) — this module's job is the structural half:
capping size and shape so a client bug can't smuggle a large arbitrary
payload through even if it tried.
"""
import json

# Matches the real feature surfaces confirmed in Stage 0 recon (vehicles/
# properties, inspections incl. the iframe-hosted diagram, tickets, backup
# export, cloud sync, maintenance templates, invitations) — not invented.
EVENT_NAMES = frozenset({
    # lifecycle
    "session_start", "page_view", "feature_open",
    # key actions
    "inspection_started", "inspection_saved", "ticket_created",
    "upload_added", "export_run", "sync_completed", "template_used",
    "diagram_edited", "invite_sent",
})

MAX_PROPS_KEYS = 8
MAX_PROP_VALUE_LEN = 200
MAX_PROPS_BYTES = 2048


def validate_props(props) -> str:
    """Returns an error message, or "" if props are acceptable."""
    if not isinstance(props, dict):
        return "event_props must be an object"
    if len(props) > MAX_PROPS_KEYS:
        return f"event_props may have at most {MAX_PROPS_KEYS} keys"
    for k, v in props.items():
        if not isinstance(k, str):
            return "event_props keys must be strings"
        if isinstance(v, (dict, list)):
            return f"event_props.{k} must be a primitive value, not nested"
        if isinstance(v, str) and len(v) > MAX_PROP_VALUE_LEN:
            return f"event_props.{k} exceeds {MAX_PROP_VALUE_LEN} characters"
    if len(json.dumps(props)) > MAX_PROPS_BYTES:
        return "event_props is too large"
    return ""
