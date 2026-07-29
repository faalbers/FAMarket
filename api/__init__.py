"""
FastAPI layer for the React UI (replacing the Streamlit `ui/` package).

Deliberately thin: every endpoint wraps an existing function in `data_layer/`,
`analysis_layer/`, `core/`, `config/` or `services/`. No business logic lives
here — if a computation is needed, it belongs in `services/` (UI-agnostic) so
it stays testable and importable without FastAPI.
"""
