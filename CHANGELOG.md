# Changelog

Notable changes to DocGraph are recorded here.

## 1.0.0 - 2026-08-26

### Breaking Changes

- Replace the handwritten legacy MCP protocol and nine-tool API with the official MCP Python SDK v2 and six focused, structured-output tools.
- Include the project-relative source path in generated document IDs so files with the same name or metadata remain distinct; existing indexes are rebuilt into the new ID format.

### Features

- Support PDF, DOCX, XLSX/XLSM, and Markdown in the core installation, with Docling, MinerU, and Marker available as optional PDF backends.
- Add schema-driven L2 extraction, stronger evidence tracking, build recovery checks, document-scoped queries, bounded graph traversal, and exact outline navigation.
- Adopt uv for dependency locking, development environments, runtime setup guidance, and CI across Python 3.11–3.13.

### Fixes

- Preserve L0/L1/L2 integrity during document replacement, parser fallback, migration failure, deleted-source reconciliation, and partial build failures.
- Improve table, figure, chunk, relation, vector, and VLM handling across normal and degraded execution paths.

### Documentation

- Reorganize the documentation by architecture, guides, references, decisions, project history, and time-bound research.
- Rewrite installation, parser selection, export, MCP, development, and operations guidance around current behavior and concise uv commands.
