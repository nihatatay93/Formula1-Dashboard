# Formula1 Dashboard Agent Rules

These rules apply to the entire repository.

1. Read the `Project Purpose`, `Current Architecture`, and `Directory
   Structure` sections of `docs/PROJECT_CONTEXT.md` before starting every
   task. Under `Technical Decisions`, read only the subsections relevant to
   the files or subsystems the task touches; read the whole section for
   cross-cutting, architectural, or database-schema changes, or whenever it
   is unclear which subsections apply.
2. Continue without contradicting the existing architectural decisions.
3. Update the relevant subsection(s) of `docs/PROJECT_CONTEXT.md` after every
   completed development change. Edit the matching subsection in place rather
   than appending a new one, and correct any subsection the change supersedes.
4. Record only information that has actually been implemented or explicitly agreed.
5. Keep planned but unimplemented work under `Next Steps`.
6. Never write passwords, tokens, cookies, credentials, or any other secret values to documentation.
7. Never delete or overwrite changes made by the user.
8. A task is not complete until `docs/PROJECT_CONTEXT.md` has been updated.
9. Every Git commit must use a concise subject and an explanatory body that
   records the scope, rationale, and relevant verification.
10. Keep the approved first-five-milestone implementation commits on
    `feature/historical-analysis-telemetry`; do not commit them to `main` or
    move `main` unless the user explicitly requests it.
