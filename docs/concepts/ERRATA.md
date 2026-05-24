# django-mcp — Errata

In-repo institutional memory for accepted bug reports and spec deviations.
GitHub Issues is the outward-facing triage queue; this log is the curated
inward-facing record agents read on every session.

## Status legend

- 🟢 **Resolved** — fix landed, verified.
- 🟡 **Diagnosed** — root cause known, fix prescription documented, no
  outstanding user input needed.
- 🔴 **Open** — symptom reproduced, root cause not yet identified.
- ⚪ **Deviation pending ratification** — execution diverged from a ratified
  phase doc; awaiting a §15 amendment to either re-align spec or formally
  accept the divergence.

## Open Questions

*(none — ERRATA-001 and ERRATA-002 both 🟢 as of 2026-05-23)*

Each entry below in `⚪` or `🔴` status SHOULD appear here as
`EOQ-NNN-ERRATA-NNN: one-line ask`, sourced from the corresponding ERRATA-NNN
id. `NNN` is monotonically increasing within this file; an assigned EOQ id is
stable across resolution.

## Index

| ID | Title | Status | First seen | Owning phase | Resolved by |
|----|-------|--------|------------|--------------|-------------|
| ERRATA-001 | `ChoiceField` with optgroup choices misreads grouped choices | 🟢 | 2026-05-22 | DMCP-01 | conservative fix in `django_mcp/schemas.py` `field_to_json_schema` ChoiceField branch (2026-05-22, same-session); promotion to first-class optgroup support tracked separately if needed |
| ERRATA-002 | Tool-name grammar rejects leading-underscore module components (`__main__`, `_internal`) | 🟢 | 2026-05-23 | DMCP-00 §5 / DMCP-02 emit path | DMCP-00 §15 amendment (2026-05-23) loosened the grammar to PEP-3131-style `id_start = ALPHA / "_"`; `django_mcp/names.py` `_validate_prefix_component` updated; `django_mcp/drf.py` `_sanitize_component` workaround removed; `tests/test_names.py` adds positive cases for `__main__.hello` and `myproj._internal.View` |

## Entries

### ERRATA-001 — `ChoiceField` with optgroup choices misreads grouped choices

- **Status:** 🟢 Resolved (conservative fix; 2026-05-22)
- **First seen:** 2026-05-22
- **Owning phase:** DMCP-01
- **Symptom:** A Django form whose `ChoiceField.choices` uses the grouped
  shape `[(group_label, [(value, label), ...]), ...]` (optgroups) crashes
  `field_to_json_schema` in `django_mcp/schemas.py` at the choice-extraction
  line (`[v for v, _ in choices]`), because optgroup entries are
  `(str, list[tuple[str, str]])` 2-tuples whose second element is itself a
  list — the comprehension unpacks `value, label = (group_label, [...])`
  successfully, then places the *group label* in the `enum` array,
  silently producing a wrong schema rather than a crash. (Re-classified
  from "crash" to "silently wrong" after re-reading the implementation
  — symptom updated 2026-05-22.) Either way, the output schema does not
  reflect the actual valid choices.

- **Root cause:** §7's original frozen table specified only the flat-
  choices shape. The schemas implementation followed the table literally
  and did not branch on the grouped shape. Diagnosed during the same
  parallel-worker pass that landed `schemas.py` (Worker C's own report
  flagged it).

- **Fix:** Conservative fix landed first via the **2026-05-22 §7
  amendment, §7.2**: detect the grouped shape (a `choices` element whose
  `[1]` is a non-string iterable) and emit `{"type": "string"}` plus a
  WARNING log line naming the field. This preserves admin boot.

  Full fix (deferred, requires a *promoting* §15 amendment to §7.2):
  flatten the grouped choices into a single `enum` array containing all
  reachable values, optionally surfacing the grouping via `oneOf` with
  per-group `title`. The promotion lands as a §15 amendment to DMCP-01
  citing this errata.

  Code location for the conservative fix:
  `django_mcp/schemas.py` :: `field_to_json_schema` ::
  `ChoiceField` branch. Sketch:

  ```python
  if isinstance(field, forms.ChoiceField):
      choices = list(field.choices)
      if any(not isinstance(c[1], str) and hasattr(c[1], "__iter__") for c in choices):
          logger.warning(
              "ChoiceField '%s' uses optgroups; emitting generic string fallback "
              "(ERRATA-001)", field.__class__.__name__
          )
          return {"type": "string"}
      return {"enum": [v for v, _ in choices]}
  ```

- **Verification:** Integration smoke run in the same session asserted
  (a) optgroup ChoiceField returns `{"type":"string"}`, (b) the
  `django_mcp.schemas` WARNING containing "optgroups" is emitted, and
  (c) flat-choices ChoiceField continues to produce the `enum`-bearing
  schema. A pytest case in DMCP-01's invariant suite (task #13) MUST
  pin both shapes via `caplog` once the harness lands.

- **Tracking:** Cited by DMCP-01 §7.2 (the load-bearing reference) and
  by the DMCP-01 §15 entry dated 2026-05-22. The conservative fix
  landed in `django_mcp/schemas.py` :: `_map_field` ChoiceField branch
  on 2026-05-22 (no git commit SHA yet — repo is pre-init; this entry
  will be retroactively updated when the first commit lands). The
  promotion to full optgroup support (if it happens) is a separate §15
  amendment; this entry stays 🟢 and a follow-up entry would track the
  promotion.

### ERRATA-002 — Tool-name grammar rejects leading-underscore module components

- **Status:** 🟢 Resolved (DMCP-00 §15 amendment 2026-05-23)
- **First seen:** 2026-05-23
- **Owning phase:** DMCP-00 §5 (grammar owner) and DMCP-02 (emit-side
  symptom)
- **Symptom:** A Django view defined in a module whose path contains a
  component starting with `_` (e.g. `__main__.hello` when a heredoc-
  embedded view is walked; `myproj._internal.views.SomeView` if a
  project uses a leading-underscore-prefixed submodule) causes
  `django_mcp.names.format` (called from `ViewInvokeRule.emit` /
  `DRFViewSetRule.emit`) to raise `ToolNameError(reason="prefix
  component must start with ALPHA (offset 0 of component 0)")`. The
  whole discovery pass aborts.

- **Root cause:** DMCP-00 §5's frozen tool-name grammar pins the
  prefix-component rule to `ALPHA *( ALPHA / DIGIT / "_" )` — leading
  `_` is rejected. The asymmetry with `target_leaf` (which allows
  leading `_` / DIGIT) was deliberate at DMCP-00 ratification, intended
  to keep tool-name prefixes "readable", but it doesn't match Python's
  own identifier rules, which permit leading underscores everywhere.
  Worker F (DRFViewSetRule) noted the asymmetry independently and added
  a `_sanitize_component()` workaround in `django_mcp/drf.py`; Worker E
  (ViewInvokeRule in `django_mcp/views.py`) and the model.search emit
  path do NOT sanitize, so the failure surfaces there.

- **Fix:** Two-step.

  1. **§15 amendment to DMCP-00 §5** (Standards Action — requires user
     ratification): relax the prefix-component grammar to match Python
     identifier rules, i.e. `( ALPHA / "_" ) *( ALPHA / DIGIT / "_" )`.
     This keeps the leading-DIGIT rejection (`1bad.User` still fails)
     while admitting `__main__`, `_internal_views`, etc.

  2. **Behaviour change in `django_mcp/names.py`** (lands after the
     amendment): update `_validate_prefix_component` to accept leading
     `_`. Update the existing `test_names.py` "ALPHA" negative-case
     message to "ALPHA or '_'" (the leading-digit case still fails;
     just the reason string changes). Remove Worker F's
     `_sanitize_component()` from `django_mcp/drf.py` — it becomes
     unnecessary and the silent normalisation it does (multiple
     leading-`_` components collapsing to `mod`) is itself a small
     correctness risk worth eliminating.

- **Verification:** 2026-05-23 amendment + code landed in the same
  session. `tests/test_names.py` parametrised positive set now includes
  `view.invoke:__main__.hello` and `view.invoke:myproj._internal.View`
  (both round-trip via `parse → str`); the `1bad.User` negative case
  continues to fail with the updated reason fragment `"ALPHA or '_'"`.
  Full 56-test suite passes after the amendment.

- **Tracking:** Resolved via DMCP-00 §15 entry dated 2026-05-23 (which
  reciprocates this errata id). EOQ-001-ERRATA-002 removed from the
  Open Questions list. Workaround removed: the prior
  `django_mcp/drf.py` `_sanitize_component` lstrip-`_` function is
  gone; dotted paths are now used verbatim.

## How to add an entry

1. Pick the next sequential `ERRATA-NNN` id (look at the Index table; do not
   reuse ids even for deleted entries — gaps are fine).
2. Append a section below with the shape:

   ```markdown
   ### ERRATA-NNN — <one-line title>

   - **Status:** ⚪ / 🔴 / 🟡 / 🟢
   - **First seen:** YYYY-MM-DD
   - **Owning phase:** DMCP-NN
   - **Symptom:** <pinned to HEAD at first-seen time with path:line cites>
   - **Root cause:** <or "TBD" while 🔴>
   - **Fix:** <resolving commit SHA if landed; proposed location + minimal
     diff sketch otherwise>
   - **Verification:** <how we proved the fix>
   - **Tracking:** <related phase docs / §15 cross-refs / GH issue link>
   ```

3. Add a row to the Index table.
4. If the entry is `⚪` or `🔴`, append an `EOQ-NNN-ERRATA-NNN` line under
   Open Questions with the one-line ask.
5. If the entry intersects a normative section of a `-NN-CONCEPTS.md`, the
   phase doc's §15 SHOULD cite the `ERRATA-NNN` id, and this entry's
   **Tracking** SHOULD reciprocate.

**Entries are permanent.** Update status to 🟢 with the resolving commit and
verification evidence; do NOT delete.

## Stealth-revert prohibition

A behaviour change that undoes ratified-and-implemented phase content while
landing under an unrelated commit's scope is a **stealth revert** and is
prohibited. If a revert is structurally necessary:

1. File an ERRATA entry FIRST (in a separate commit landing only the errata)
   with status ⚪.
2. Land the unrelated change citing the `ERRATA-NNN` id in the commit subject.
3. Flip the status to 🟡 or 🔴 as appropriate.
4. The phase doc's §15 ALSO gets a dated entry pointing at the errata.

This makes stealth reverts impossible by construction — every revert produces
(a) an errata entry, (b) a §15 entry, (c) a commit-subject citation.
