"""
Parameter Reference page — a read-only browse of every `config/param_hints.py`
entry, the system's one canonical hint registry (see CLAUDE.md). Entries are
grouped into collapsible categories (Price, Valuation, Quality, … , Statement
item, Score), each collapsed by default; expand one to read every parameter's
name, key, unit and the what-it-is / how-to-use / peers notes.

Laid out for easy reading: a narrow content column (short lines), large bold
parameter names, one bordered card each, and roomy line spacing (the latter via
the `st-key-paramref` rule in app.py). No `url_path` — internal sidebar page.
"""

from __future__ import annotations

import streamlit as st

from config.param_hints import PARAM_HINTS, hint_markdown

# Group keys by category, preserving the registry's insertion order within each group
# (and the order categories first appear, which is already the logical reading order).
_by_cat: dict[str, list[tuple[str, dict]]] = {}
for _key, _h in PARAM_HINTS.items():
    _by_cat.setdefault(_h.get("category", "Other"), []).append((_key, _h))


def _matches(key: str, h: dict, q: str) -> bool:
    if not q:
        return True
    how = h.get("how_to_use")
    hay = " ".join([key, h.get("name", ""), h.get("category", ""), h.get("what_it_is", ""),
                    " ".join(how if isinstance(how, list) else [str(how or "")]),
                    str(h.get("vs_peers", ""))]).lower()
    return q in hay


st.title("Parameter Reference")
st.caption(f"Every parameter the system knows about — {len(PARAM_HINTS)} in total. "
           "Pick a category to open it.")

# Keyed wrapper so app.py can give this page roomier line spacing + body size; a narrow
# content column keeps lines short (easier to read). Everything renders inside both.
with st.container(key="paramref"):
    _content = st.columns([3, 2])[0]  # ~60% width → comfortable line length
    with _content:
        _q = st.text_input("search", placeholder="🔍  search name, key or description…",
                           label_visibility="collapsed").strip().lower()

        _any = False
        for _cat, _items in _by_cat.items():
            _shown = [(k, h) for k, h in _items if _matches(k, h, _q)]
            if not _shown:
                continue
            _any = True
            # Collapsed by default; a search opens the categories that still match.
            with st.expander(f"{_cat}   ({len(_shown)})", expanded=bool(_q)):
                for _key, _h in _shown:
                    with st.container(border=True):
                        # The name + key/unit are this page's own chrome; the hint body
                        # (what it is / how to use / Peers) is rendered through the one
                        # canonical formatter so its style is never re-coded here.
                        st.markdown(f"#### {_h.get('name', _key)}")
                        _unit = _h.get("unit")
                        st.caption(f"key  `{_key}`" + (f"  ·  unit  `{_unit}`" if _unit else ""))
                        st.markdown(hint_markdown(_key, header=False))

        if not _any:
            st.info("No parameters match your search.")
