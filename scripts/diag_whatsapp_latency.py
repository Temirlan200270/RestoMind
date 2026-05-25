"""Diagnose WhatsApp latency for one trace or phone (queue wait + pipeline stages).

Usage:
    python scripts/diag_whatsapp_latency.py --org-id 1 --trace-id abc123
    python scripts/diag_whatsapp_latency.py --org-id 1 --phone +77051310837
    python scripts/diag_whatsapp_latency.py --org-id 1 --hours 24

Prints:
  - latest trace_id for phone (if --phone)
  - trace timeline entries (SystemEvent + ChatLog)
  - pipeline_latency_logs p50/p95 for recent samples
  - grep hints for Render logs (queue_wait_ms, rm_stage_ms.llm, fast→strong)
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.db.session import async_session_factory
from app.services.phone_normalize import canonical_user_phone
from app.services.pipeline_latency import get_latency_summary
from app.services.trace_timeline import build_trace_timeline, latest_trace_for_phone


def _print_render_hints(trace_id: str | None) -> None:
    tid = trace_id or "<trace_id>"
    print("\n--- Render log grep hints ---")
    print(f"  queue wait:     grep 'queue_wait_ms' | grep '{tid}'")
    print(f"  pipeline stages: grep 'rm_stage_ms' | grep '{tid}'")
    print("  LLM rerun:      grep 'fast→strong rerun'")
    print("  serializer:     grep 'chat_serializer.deferred' OR 'chat_serializer.queued'")
    print("  ARQ fallback:   grep 'BackgroundTasks fallback'")


async def _run(
    org_id: int,
    *,
    trace_id: str | None,
    phone: str | None,
    hours: int,
) -> int:
    async with async_session_factory() as db:
        resolved_trace = (trace_id or "").strip() or None
        canon_phone = canonical_user_phone(phone) if phone else None

        if resolved_trace is None and canon_phone:
            resolved_trace, _conv = await latest_trace_for_phone(
                db,
                org_id=org_id,
                phone=canon_phone,
            )
            print(f"phone={canon_phone} latest_trace_id={resolved_trace or '-'}")

        if resolved_trace:
            timeline = await build_trace_timeline(db, org_id=org_id, trace_id=resolved_trace)
            print(f"\n--- trace timeline ({timeline.get('total', 0)} entries) ---")
            for entry in timeline.get("entries") or []:
                kind = entry.get("kind") or entry.get("type") or "?"
                at = entry.get("at") or entry.get("created_at") or "-"
                summary = entry.get("summary") or entry.get("content") or ""
                if isinstance(summary, str) and len(summary) > 120:
                    summary = summary[:120] + "…"
                print(f"  {at}  [{kind}]  {summary}")

        summary = await get_latency_summary(db, org_id, hours=hours)
        print(f"\n--- pipeline_latency_logs (last {hours}h) ---")
        if not summary:
            print("  (no samples — check PIPELINE_LATENCY_ENABLED and recent traffic)")
        else:
            for row in summary:
                print(
                    f"  {row['stage']:8s}  p50={row['p50_ms']:5d}ms  "
                    f"p95={row['p95_ms']:5d}ms  max={row['max_ms']:5d}ms  n={row['samples']}",
                )
            print(
                "\n  Note: queue_wait is logged in restomind.pipeline rm_stage_ms.queue_wait "
                "(Redis rm:wa_enqueued:*); not persisted in pipeline_latency_logs yet.",
            )

        _print_render_hints(resolved_trace)
        if resolved_trace:
            print("\n  Sample JSON log filter:", json.dumps({"trace_id": resolved_trace}))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose WhatsApp latency / trace")
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--trace-id", type=str, default=None)
    parser.add_argument("--phone", type=str, default=None)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.org_id, trace_id=args.trace_id, phone=args.phone, hours=args.hours)))


if __name__ == "__main__":
    main()
