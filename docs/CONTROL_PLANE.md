# Control Plane Roadmap

This document tracks the control-plane workstream for RestoMind without adding `RAG`.

## Goal

Make AI behavior:

- reproducible;
- traceable;
- testable over time;
- safer to change per tenant and per rollout.

## Phase 1: State Foundation

Status: in progress.

Implemented:

- formal conversation state rules in `app/services/conversation_state.py`;
- durable `conversation.state_changed` events emitted from session updates;
- admin takeover/release and persistent AI pause now update DB state durably, not Redis only.

Remaining:

- move all state transitions to one service entrypoint;
- add timeout and recovery transition helpers;
- expose state transition history in admin timeline.

## Phase 2: Trace And Timeline

Status: **in progress** (foundation shipped 2026-05-21).

Implemented:

- `app/services/trace_context.py` — `contextvars` для `trace_id` / `conversation_id`; `build_trace_id(seed)` (WhatsApp `message_id` как seed); `trace_log_prefix()` для structured logs;
- WhatsApp inbound: `trace_context()` в `process_message` / `process_with_retry` ([`webhooks.py`](app/api/webhooks.py)); seed из `whatsapp_message_id`;
- ARQ: `trace_id` пробрасывается через enqueue ([`task_queue.py`](app/services/task_queue.py)) и worker kwargs ([`worker.py`](app/worker.py));
- Durable events: `emit_event` автоматически обогащает payload через `enrich_payload_with_trace` ([`system_events.py`](app/services/system_events.py));
- Order draft: `stamp_order_meta_trace` пишет trace в `items_json.order_meta` для join с timeline;
- AI logs: `[trace_id=…]` prefix в [`ai_brain.py`](app/services/ai_brain.py);
- Typed WS helpers: `publish_order_event`, `publish_chat_event`, `publish_human_event`, `publish_state_event` — канонические поля trace в payload;
- Tests: [`tests/test_control_plane_trace.py`](tests/test_control_plane_trace.py).

Remaining (still paper / not done):

- full propagation through iiko client calls (structured `[trace_id=…]` logs in [`iiko_client.py`](app/integrations/iiko_client.py)), outbound WhatsApp send ([`whatsapp.py`](app/integrations/whatsapp.py)), operator admin replies ([`chats.py`](app/api/admin/chats.py) reuses conversation trace + stamps `ChatLog.meta_json`);
- causal chain fields (`parent_event_id`, `caused_by`) on `BusinessEvent` → `SystemEvent.payload_json`;
- `GET /api/admin/intelligence/trace-timeline?trace_id=` — merged SystemEvent + ChatLog timeline ([`trace_timeline.py`](app/services/trace_timeline.py));
- admin search/filter UI by `trace_id` (full timeline panel in admin — not done);

## Phase 3: Replay Harness

Planned:

- persist replayable inbound scenario snapshots;
- add golden conversations dataset;
- build scorecards for intent, order correctness, escalation quality, latency, and cost.

## Phase 4: Policy Versioning

Planned:

- prompt, policy, and routing versions;
- tenant-aware rollout flags;
- rollback by version, not by ad hoc code edits.

## Phase 5: Simulation

Planned:

- offline batch replays against historical scenarios;
- compare model, prompt, and policy candidates before deploy;
- measure quality and cost deltas safely.
