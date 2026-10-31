# Browser reliable synchronization

Schema v4 adds a durable receipt table with a unique event_id. Receipt and activity insert commit in the same transaction. Repeated IDs with identical payload are acknowledged as duplicate; changed payload is rejected. Old clients without IDs remain supported without deduplication guarantees. Keep IDs globally unique (UUID) and retain the original payload, including timestamps, on retries.

Batch sessions are validated independently. data.acknowledgements contains index, event_id and status: inserted / duplicate / rejected / retry. Only inserted and duplicate confirm durable receipt. Errors retain their existing index/reason fields. Rejected events require user or client correction; never blindly discard them.

clients/browser-queue.mjs is an integration module, not an installed browser extension. Supply durable load/save methods using chrome.storage.local, use one instance per key, call enqueue once per completed event, and flush on startup and retry alarms. The send callback must POST {sessions} to /api/v1/activity/browser with existing authentication and return parsed JSON only after checking HTTP success. A failed request retains the queue. Confirmed IDs alone are removed, including when new events arrive during a request. No automatic queue truncation is performed; the integrating extension should surface storage quota failures and queue size.

This repository contains no extension source; actual capture, alarm scheduling, permissions and integration acceptance remain pending. Tests use an in-memory storage adapter that survives queue instance recreation, not a real browser restart.

Validation on Windows 2026-10-31: pytest 66 passed, Ruff passed; Node queue tests 2 passed. Real lock/sleep/wake/reboot and actual extension integration remain pending. Issue #1 must stay open until those acceptance steps are completed. Desktop remains the duration authority; browser rows enrich content only.

