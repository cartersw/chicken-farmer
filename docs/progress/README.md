# Project progress

This folder tracks completed work, verification, blockers, and the remaining CS2 vision imitation-learning milestones.

**Last updated: 2026-09-07. Current milestone: initial data tooling implemented and exercised on three real demos.**

The project can extract and normalize player commands and prepare render jobs. It has retained **4,933,316 reconstructed commands** and **3,511,650 valid alive-player angular transitions**. There are **no captured CS2 replay frames, verified frame/action training samples, or trained models yet**. All three complete-demo quality reports currently fail for documented input issues.

| Document | Purpose |
| --- | --- |
| [Current status](STATUS.md) | Everything built, what works, what is only partly verified, and where the artifacts live |
| [Next steps](NEXT_STEPS.md) | Ordered work items with dependencies and completion criteria |
| [Completion history](CHANGELOG.md) | Dated record of delivered work, checks, and decisions |

The [project handoff](../../CS2_Vision_Imitation_Learning_Project_Handoff.md) remains the specification. The [initial run report](../INITIAL_RUN.md), [data schema](../DATA_SCHEMA.md), [rendering guide](../RENDERING.md), and [alignment guide](../ALIGNMENT.md) contain supporting details. [The repository README](../../README.md) has setup and command examples.

## Keeping this up to date

After a meaningful implementation or verification milestone:

1. Update `STATUS.md` to describe the current behavior and its evidence.
2. Check off completed items in `NEXT_STEPS.md`; record what proves they are complete.
3. Add a dated entry to `CHANGELOG.md` with changed components, checks, output locations, and unresolved issues.
4. Record new parser/schema/normalizer/renderer versions when they change. Keep earlier history and raw artifacts intact.

Use **implemented**, **verified on real demos**, **verified with synthetic fixtures**, **blocked**, and **not implemented** precisely. A passing unit test or renderer dry run does not prove a complete replay-to-training-data run.
