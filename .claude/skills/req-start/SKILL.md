---
name: req-start
description: Start work on one requirement. Restates its acceptance criteria, checks dependencies, creates the branch and proposes a plan.
argument-hint: "<REQ-ID>"
disable-model-invocation: true
---

Start requirement `$ARGUMENTS`. Change no files in this skill; the only side effect is the new
branch. If no ID was given, read `PROGRESS.md`, name the next `todo` row and stop.

1. **Find the rows.** Read every row for the ID in `PROGRESS.md`.
   - An ID can have two rows (RAG-12 is #4 and #32, SA-02 is #5 and #17). Work on the first one
     that is not `done`, and say which row that is.
   - A row can hold two IDs (`RAG-08 + NFR-03 (partial)`). Cover both.
   - If the row is already `done`, say so and stop.
2. **Restate the criteria.** Find `^| <ID> |` in `docs/PRD.md` and quote the acceptance criteria.
   Then say what the PROGRESS row puts in or out of scope; `(partial)` and `(crude)` rows narrow
   the criteria. Rows with no PRD ID (`FOUND-3`, `A2A-05 prep`) have no PRD row, so the row's scope
   text is the criteria. Say that this is what you used.
3. **Check dependencies.** The PRD has no dependency column, so infer them from IDs and `#N`
   references in the row text, and from tier order: lower-numbered rows in the same tier come
   first. Label them "inferred". List each with its status. If any is not `done`, stop and ask the
   user whether to go on; do not create the branch.
4. **Create the branch.** Stop if `git status --short` shows changes. Otherwise run `git fetch
   origin`, then `git switch -c req/<ID>-<slug> origin/main`, with a short kebab-case slug.
5. **Propose a plan.** End with a plan that maps each criterion to the change and to at least one
   tagged test. Do not start implementing until the user approves it.
