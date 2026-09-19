# AI Memory Store

This directory stores persistent, Git-tracked memory records for AI agents, including architectural decisions, historical learnings, and operational context.

## File Types
- `decisions.md`: Key design and architectural decisions made over the lifecycle of the project.
- `learnings.md`: Operational discoveries, bug workarounds, and optimization insights.
- `context.md`: High-level domain context, pipeline architecture, and invariant rules.

## Managed by `git_rag.py`
You can append records manually or using:
```bash
python -B scripts/git_rag.py record "Decision Title" "Detailed rationale..." --target=markdown --file=decisions.md
```
