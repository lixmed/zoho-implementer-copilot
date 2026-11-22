# ADR 0004: Planner, LLM drafts and server decides

**Status:** accepted (2026-11-23)

## Decisions
1. **The LLM runs in Claude Code via MCP** (no extra API cost). The server owns the schema, the validation, the coverage, versioning and rendering. Options considered: our own Claude API call now (rejected for Phase 2 on cost; it arrives in Phase 6 for evals).
2. **Inputs:** .md, .txt, .eml, .docx and .pdf from the start (the user chose this over text-only). Email quotes, signatures and greetings are stripped. Contact details are masked.
3. **Workspace:** a folder the user chooses, outside the git repo, so client material can never be pushed.
4. **Coverage is computed, not claimed.** Citations are the only way to cover a requirement, and unaddressed units block saving.
5. **Delivery is derived** from the Phase 0 table, not chosen by the LLM.
6. **No approve tool in MCP.** Approval is a CLI action with a typed confirmation. It is stored with a SHA-256 hash, so a file edited after approval no longer counts as approved.
7. **Stable item ids across versions** (the agent is told to keep them). This is what makes change-request diffs meaningful.

## Trade-offs
- Sentence splitting is heuristic. A unit can be slightly too big or too small. Ids stay stable as long as existing files aren't edited.
- PDF text extraction depends on the PDF. Scanned PDFs (images) yield nothing; OCR is out of scope.
