---
name: make_filters_setup
description: This skill only gets triggered with the /make_filters_setup command. Creates or refreshes .claude/docs/screening_system.md (the knowledge base /make_filters reads) from the current code and targeted web research.
disable-model-invocation: true
---

# make_filters_setup

Creates or updates **`.claude/docs/screening_system.md`** — the compressed screening
knowledge base that `/make_filters` reads first, and that the user may ask Claude to
read for session priming. This skill is the ONLY sanctioned way to regenerate that
doc; ad-hoc rewrites lose the living §7 content.

## When to use

Only when the user types `/make_filters_setup`. Typical moments:

- after substantial analysis-layer / filter-system changes (new params, changed
  scoring rules, changed screen-type applicability, new operators or variants);
- when the user wants the web-research knowledge (§8) refreshed;
- when the doc is missing (fresh clone) and needs a full rebuild.

## Sources of truth (the CODE wins over the doc, always)

| What | File |
|---|---|
| Params, units, usage hints | `config/param_hints.py` |
| Screen types + per-type applicability | `services/filter_registry.py` |
| Block model, operators, `.filt` format | `services/filter_engine.py` |
| Scoring rules → goodness ("Score" variant) | `analysis_layer/scoring_rules.py` |
| Screen-type keys + classifier | `analysis_layer/screen_type.py` |
| Authoring procedure (doc §6 mirrors it) | `.claude/skills/make_filters/SKILL.md` |
| Structural validation the doc references | `scripts/validate_filt.py` |

## Procedure

1. **Read the doc** (`.claude/docs/screening_system.md`) fully. If it does not
   exist, do a full rebuild following the Section layout below, then continue at
   step 6.
2. **Read the code sources** above and **diff them against the doc's claims**:
   - params added / removed / renamed; changed `unit` values;
   - changed applicability sets (which screen types a metric applies to);
   - changed `DEFAULT_RULES` (shape / anchor / bands / overrides);
   - new operators, compare variants, or growth windows;
   - changed screen types or their classification.
3. **Update only what drifted, in place.** Preserve the §1–§9 structure and the
   doc's style: dense, table-heavy, written for Claude (not the user), code stays
   source of truth.
4. **§7 is the LIVING home for N/A traps + calibration values — never regenerate
   it.** Preserve its accumulated entries verbatim. Only APPEND new entries the
   user has explicitly agreed to (e.g. fresh Filter Fail findings from this
   session). Same protection applies to any per-param calibration notes elsewhere.
5. **Keep §6 consistent with the make_filters SKILL.md** — that skill is canonical
   for the procedure; §6 is its condensed mirror. If the skill's procedure changed,
   re-condense §6 (don't invent steps the skill doesn't have).
6. **Web refresh — targeted, not wholesale.** Only when the user asked for a
   knowledge refresh, or a code change touches a §8 topic. A handful of searches
   max, on the §8 topic areas (momentum/trend templates, estimate revisions/PEAD,
   forensic scores, GARP/quality, insider signals, dividend safety). Integrate only
   genuinely NEW findings; keep §8 compressed and practical (what informs threshold
   choices). Note the research date.
7. **Keep the header contract intact**: audience = Claude; read only on explicit
   user ask or via a skill reference; never auto-load at session start; code wins
   on disagreement. Update the doc's date and the source-file table if files moved.
8. **Report a short changelog** to the user: what changed in the doc and why
   (which code drift / which new research). If nothing drifted, say so and change
   nothing. Do NOT commit — the user handles git.

## Section layout (for a full rebuild)

1. **§1** System context + the 9 screen types and their metric philosophy
2. **§2** Unit conventions table (the #1 threshold trap)
3. **§3** Full parameter reference by category — key, unit, applicability,
   scoring-rule direction, usage notes, gotcha pointers
4. **§4** Scoring rules → `_goodness` (shapes, anchors, the "Score" variant)
5. **§5** Filter block model, operators, NULL semantics, column resolution,
   `.filt` JSON format
6. **§6** Authoring procedure — condensed mirror of the make_filters SKILL.md,
   including the validate + dry-run snippet (`scripts/validate_filt.validate_payload`
   + scoped per-block counts)
7. **§7** N/A gotcha table + calibration values — marked as the canonical living
   home (see step 4 above)
8. **§8** Domain knowledge from web research, with the research date
9. **§9** Filter-thesis → Output-sort guidance table
