## Summary of Changes
<!-- Provide a concise description of the feature, fix, or refactor. -->

## AI Prompt & Model Context
- **Primary AI Agent / Model**: <!-- e.g., Claude 3.7 Sonnet, Gemini 3 Flash/Pro -->
- **Task Goal**: <!-- Short description of the prompt goal -->

## Verification & Guardrail Checklist
- [ ] **Automated Tests**: Ran `python -B -m unittest discover -s tests -p "test_*.py"` (all tests passed).
- [ ] **Bytecode Guard**: Verified all added/edited `.py` files include the mandatory header guard (`PYTHONDONTWRITEBYTECODE=1`).
- [ ] **Video Safety**: Any pipeline tests use synthetic data or max 2-second mockups (no heavy media files committed).
- [ ] **Path Portability**: No machine-specific absolute paths (`C:\xampp\...`) hardcoded in logic.
- [ ] **Code Integrity**: Preserved existing comments, variables, and indentation styles.
- [ ] **Git RAG Decision Record**: Recorded major architectural decisions in `.agents/memory/decisions.md` (if applicable).
