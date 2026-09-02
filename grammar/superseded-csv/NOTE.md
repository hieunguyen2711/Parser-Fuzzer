# Superseded — not the assigned target

This directory holds a complete Step 1 deliverable for **libcsv / CSV**, done
before the assigned library was confirmed as **mxml / XML**. It is kept only
so the work and its reasoning are not lost; nothing here is part of the
submission, and `target/libcsv` should be removed alongside it.

The one idea worth carrying forward is the check that killed the CSV plan: ask
early whether the library *rejects anything at all*, because a parser that
accepts every input gives the refinement loop a constant instead of a signal.
That question is asked again for mxml in `../README.md`.
