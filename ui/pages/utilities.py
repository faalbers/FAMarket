"""
Utilities page — a home for one-off helper tools.

Each utility is a self-managed collapsible section (a header button toggling a
session flag, body rendered under `if open:`), the same pattern the Settings page
uses for the calibration tuner. New utilities slot in as more toggle + `if` blocks.

First utility: Email a symbol selection (ui/email_report.render()) — compose an
email about a saved `.syms` selection; Send currently writes a Markdown preview.
"""

from __future__ import annotations

import streamlit as st

from ui import email_report

st.title("Utilities")


# -- Email a symbol selection ---------------------------------------------- #
def _cb_toggle_email() -> None:
    st.session_state["util_email_open"] = not st.session_state.get("util_email_open", False)


_email_open = st.session_state.setdefault("util_email_open", False)
st.button(f"{'▾' if _email_open else '▸'}  Email a symbol selection",
          key="util_email_toggle", on_click=_cb_toggle_email, width="stretch")
if _email_open:
    email_report.render()
