# Agent Notes

This repository maintains human-readable project maps in the root directory.

Any structural change must update the maps in the same change set:

- Update `ARCHITECTURE.md` when adding modules, moving responsibilities, changing entry points, or changing dependency direction.
- Update `DATAFLOW.md` when changing runtime data shapes, persistence semantics, external integrations, or producer/consumer relationships.
- Update `DECISIONS.md` when choosing one architecture or integration approach over meaningful alternatives.
- Update `PENDING.md` when a task changes handoff state, risk tier, or known-facts context.

Do not rely on memory when updating these files. Scan the current code first, then document only what the code actually does.
