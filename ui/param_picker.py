"""
Shared parameter picker (Topics 5 + 6).

The popover metric browser used by BOTH pages, so they look and behave exactly
the same:

  * Filter page — pick ONE metric into a filter block field.
  * Output page — add / remove result columns.

Layout (one popover): a search box, a category caption per group, then one row
per metric — ``[name button | ▸ info toggle]``. The ▸ expands that metric's
`param_hints` HTML inline below the row (`.fam-hi` in app.py), collapsed by
default, one open at a time. Hovering the closed trigger shows the current label.

Two selection modes via ``close_on_pick``:

  * ``True`` — single-select: clicking a name fires ``on_pick`` and remounts the
    popover closed. Streamlit has no close-popover API, so the picker bumps a
    nonce that re-keys its wrapping container, which remounts the popover shut.
  * ``False`` — multi-select: clicking a name toggles membership via ``on_pick``
    and the popover stays open; selected rows are primary-styled so the open
    browser doubles as the current selection.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from config import param_hints
from ui import filter_registry as R


# --------------------------------------------------------------------------- #
# hint HTML for the inline info panel
# --------------------------------------------------------------------------- #
def hint_html(base: R.Base) -> str:
    """The param's hint as HTML for the picker's inline info panel (.fam-hi).

    Streamlit's own tooltips proved uncontrollable inside a popover (position
    flips per option, offsets ignored), so each param row carries a ▸ toggle
    that expands this HTML in-flow below the row instead. The formatting itself
    lives in `config.param_hints.hint_html` — this just supplies the picker's
    Base as the fallback label for keys with no hint yet.
    """
    return param_hints.hint_html(
        base.key,
        fallback={"name": base.name, "category": base.category, "unit": base.unit},
    )


# --------------------------------------------------------------------------- #
# picker
# --------------------------------------------------------------------------- #
def _toggle_info(info_key: str, k: str) -> None:
    """Expand/collapse one param's inline info (only one open per picker)."""
    st.session_state[info_key] = None if st.session_state.get(info_key) == k else k


def _on_pick(k: str, on_pick: Callable[[str], None], nonce_key: str, close: bool) -> None:
    on_pick(k)
    # Re-key the picker's wrapping container so the popover remounts closed —
    # Streamlit has no API to close a popover programmatically.
    if close:
        st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1


def render(
    container,
    *,
    opt_keys: list[str],
    label: str,
    keyp: str,
    category_of: Callable[[str], str],
    name_of: Callable[[str], str],
    info_html_of: Callable[[str], str],
    search_text_of: Callable[[str], str],
    is_selected: Callable[[str], bool],
    on_pick: Callable[[str], None],
    close_on_pick: bool,
    exclude_selected: bool = True,
    trigger_width: str = "stretch",
) -> None:
    """Render the popover param browser inside ``container``.

    The caller supplies the option keys (already in display order) plus small
    accessors so the same widget serves both the base-keyed Filter picker and the
    concrete-column Output picker. ``exclude_selected`` (default) hides any option
    `is_selected` reports as already chosen — so the Filter picker drops the block's
    current metric and the Output picker drops columns already in the set.

    ``trigger_width`` is the popover button's width ("stretch" to fill its column —
    the default, used where pickers sit in an aligned grid; "content" to hug the
    selected-param label, e.g. the standalone Fundamentals picker). The open panel
    itself is always sized to its content via CSS (see app.py), independent of this.
    """
    nonce_key = f"{keyp}:nonce"
    wrap = container.container(key=f"{keyp}:wrap{st.session_state.get(nonce_key, 0)}")
    # No help= on the trigger: the hint is browsable via each row's ▸ toggle.
    with wrap.popover(label, width=trigger_width):
        q = st.text_input("search", key=f"{keyp}:q", placeholder="🔍 search…",
                          label_visibility="collapsed").strip().lower()
        info_key = f"{keyp}:info"
        last_cat = None
        shown = 0
        for k in opt_keys:
            if exclude_selected and is_selected(k):
                continue
            if q and q not in search_text_of(k):
                continue
            cat = category_of(k)
            if cat != last_cat:
                st.caption(cat)
                last_cat = cat
            shown += 1
            expanded = st.session_state.get(info_key) == k
            row = st.columns([5, 1], gap="small")
            row[0].button(name_of(k), key=f"{keyp}:opt:{k}",
                          on_click=_on_pick, args=(k, on_pick, nonce_key, close_on_pick),
                          type="primary" if is_selected(k) else "secondary", width="stretch")
            row[1].button("▾" if expanded else "▸", key=f"{keyp}:nfo:{k}",
                          on_click=_toggle_info, args=(info_key, k),
                          help="Show / hide info", width="stretch")
            if expanded:
                st.markdown(f"<div class='fam-hi'>{info_html_of(k)}</div>", unsafe_allow_html=True)
        if shown == 0:
            st.caption("Nothing matches your search." if q else "Nothing left to add.")


# --------------------------------------------------------------------------- #
# scroll-to-current helper (page-level; render once near the pickers)
# --------------------------------------------------------------------------- #
def scroll_to_current() -> None:
    """Scroll a freshly opened picker popover to its current (primary) entry.

    Streamlit has no scroll API, so a zero-height component watches the parent
    document for a popover body appearing and centers the highlighted button
    inside it — scrolling only the popover's own scroll container (never the
    page). The param pickers are the only popovers with a primary button.
    """
    st.iframe(
        """
        <script>
        const doc = window.parent.document;
        const center = () => {
            for (const body of doc.querySelectorAll('[data-testid="stPopoverBody"]')) {
                if (body.dataset.famScrolled) continue;
                const cur = body.querySelector('[data-testid="stBaseButton-primary"]');
                if (!cur) continue;
                body.dataset.famScrolled = "1";
                const b = body.getBoundingClientRect(), c = cur.getBoundingClientRect();
                body.scrollTop += c.top - b.top - body.clientHeight / 2 + cur.clientHeight / 2;
            }
        };
        new MutationObserver(center).observe(doc.body, {childList: true, subtree: true});
        center();
        </script>
        """,
        height=1,  # st.iframe rejects 0; 1px is effectively invisible
    )
