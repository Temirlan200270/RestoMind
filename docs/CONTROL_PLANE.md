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

Planned:

- propagate `trace_id` across webhook, ARQ, AI, iiko, outbound, operator actions;
- add causal chain fields to durable events;
- unify order/chat timeline around one operational stream.

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
