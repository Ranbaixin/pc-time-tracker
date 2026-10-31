# Browser sync implementation plan

Approved design: stable UUID per event, database uniqueness and payload conflict detection, per-item acknowledgements, durable client queue. Legacy requests remain accepted without retry guarantees. Extension source is absent; ship a reusable adapter and document integration as pending.

1. Add regression tests for repeated batches, conflicting IDs and mixed invalid objects; observe failure.
2. Add backed-up schema v4 receipt table and transactional receipt/activity inserts. Validate items independently.
3. Add a storage-adapter browser queue with serialized mutations and explicit acknowledgements; test restart, retries and partial acknowledgements using Node.
4. Run full Windows pytest, Ruff and client tests; review diff; push branch and sync local through three-way application preserving existing changes.

Real Windows sleep/lock/reboot and actual extension integration remain acceptance work; do not close issue #1 prematurely.
