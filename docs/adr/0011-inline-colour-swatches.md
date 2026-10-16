# ADR 0011 — Colour swatches and a picker write colours back into the source

- Status: accepted (2026-10-16)
- Context: Changing a colour meant finding the literal in the code and typing a new
  value. Every colour literal in a scene (`color=BLUE`, `fill_color="#FF6B6B"`,
  `set_color(GREEN)`) can be shown as a square beside its line and edited by clicking
  it, with no separate inspector. Timeline identity is the constraint: a call's
  fingerprint (ADR 0007) hashes the whole call minus its duration, so a colour value
  inside a `play(...)` call would change the identity and orphan that call's patches.

## Decision

1. **Colour literals are found by a pure module** (`color_sites.py`). `ast` gives UTF-8
   byte spans, which is what Scintilla uses, so an edit is an exact splice. Only
   literals on one line are reported. Names come from a table that mirrors
   `manimlib.constants`; a test compares the table with the installed package.
   Computed colours (`color=my_var`, `interpolate_color(...)`) get no swatch.
2. **A picked colour is spelled as a manim name when one matches**, otherwise as a
   quoted hex string in the quote style of the literal it replaces (`literal_for`).
   The plain name wins over its `_C` alias.
3. **Colour values do not change a call's timeline identity.** The fingerprint payload
   replaces every colour value with `None` (`neutralise_colors`): colour keyword values
   and the arguments of colour setters. Keyword presence still counts, so adding or
   removing a colour changes the fingerprint; changing which colour does not. This
   amends ADR 0007's "changing its animations changes its identity" for colours only.
4. **The editor draws the swatches** (`editor/color_swatches.py`). Each is a painted
   `SwatchButton` (14 px), not a `QPushButton`, because the app-wide stylesheet gives
   push buttons a minimum size that would override it. Swatches sit after their line's
   text, positioned from Scintilla's point queries. They are repositioned on
   `updateUi` (scroll, caret) and rescanned 150 ms after an edit. Broken source shows
   none.
5. **The picker writes through the editor buffer.** Clicking a swatch opens a
   non-modal colour dialog. Each change replaces the literal with `setSel` and
   `replaceSel`, so writes join undo, the modified flag and the reload path, the same
   as typing. One picker session is one undo step. Before every write the controller
   checks that the bytes at the span still read as expected; if they do not, it never
   writes over them and rescans.

## Consequences

- **One-time identity change for existing edits.** Any call that contains a colour
  value gets a new fingerprint. A timeline patch anchored on such a call is orphaned
  the first time a scene loads after this change. Orphans are kept and reported, never
  moved (ADR 0007). The only sidecar in the repo, `scenes/Welcome.studio.json`, has no
  patches, so nothing is affected there.
- Recolouring is an ordinary buffer edit: it marks the file modified and reloads the
  preview like typing does. The timeline keeps its patches.
- Positions come from Scintilla's point queries, so swatches are only as accurate as
  those queries under scrolling and zoom. Lines wider than the viewport hide their
  swatches rather than clipping them.
- The picker is `QColorDialog`, not a palette-aware popup. Manim palette names and the
  scene palette (`palettes.py`) are not offered yet; that is a follow-up.
- Multi-line colour literals and computed colours are skipped by design.
