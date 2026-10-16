# ADR 0012 — Mobject inspector: gears beside mobject lines and a discovered property panel

- Status: accepted (2026-10-16). Supersedes the swatch UI of ADR 0011 (its fingerprint
  decision stands).
- Context: ADR 0011 gave each colour literal a swatch. Users want every property of a
  mobject (radius, fill, stroke, position, font, font size, gradients...) edited from
  the editor, for every ManimGL class, not a hand-picked few. A hand-written catalogue
  would go stale with each manimlib release and would cover only some classes.

## Decision

1. **Discover the API at runtime, in a subprocess.** `schema_probe` imports manimlib in
   its own process (`python -m manim_studio.schema_probe`, or the frozen `--schema-probe`
   flag, ADR 0005) and prints JSON: for each `Mobject` class in the MRO of an exported
   class, its own `__init__` parameters with their real defaults, its chainable methods
   (those whose body returns `self`), and the hex value of each colour constant. The
   editor never imports manimlib (AGENTS rule 2). An AST scan (`api_index`) cannot serve:
   it has no signatures, and keyword arguments flow through `**kwargs` and non-literal
   defaults (`RED`, `TAU / 4`).
2. **Cache by manimlib version.** The output is cached in the user cache directory
   (`mobject_schema.json`) and reused while the installed `manimgl` version matches. A
   miss runs the probe in the background (about 4 s); gears and the inspector wait for it
   and never block the GUI thread (`editor/schema_cache.py`).
3. **Flatten along the MRO.** A class's fields are its own parameters plus those of its
   bases, most derived first; a subclass's parameter shadows the base's of the same
   name, as `**kwargs` forwarding does at runtime. `**kwargs` entries are skipped.
4. **Widgets are inferred, not tabulated.** `ui_kind` decides from the annotation and the
   default (bool, int, float, colour, colour list for `*colors`, text), with name hints
   for `*opacity`, `font` and `font_size`. Anything else, and any source value that does
   not fit the widget, becomes an expression field showing the Python text, so nothing
   the source says is lost.
5. **Gears mark mobject lines.** A target is a constructor binding `name = Ctor(...)`
   plus the chained calls on it and the later `name.method(...)` statements in the same
   block. Each line of a target gets a gear (`inspector_gear.py`, replacing the swatch).
   A click re-reads the target from the buffer; a stale click rescans instead of opening
   a target that has moved. Constructors inside `self.play(...)` and `.animate` are not
   targets: editing them would change the `play()` fingerprint and orphan timeline
   edits (ADR 0007, ADR 0011).
6. **Edits are minimal splices, checked by parsing.** `call_edits` sets, removes and adds
   arguments as UTF-8 byte splices, so comments and layout survive. Changes are applied
   one at a time, each against a fresh scan, so indices never go stale. Chained calls are
   added last. The result must parse as Python, or nothing is written.
7. **Apply goes through the window, like `_update_code`.** The panel only emits the
   change list. The window checks that the target still reads the same, applies the
   changes as one undo step, and, when the buffer was clean, saves and reloads the
   preview. With unsaved edits the change goes to the buffer only, and the status line
   says to save.
8. **Rows say what the source does.** A row's check box means "the source sets this
   argument". Checked and changed writes the argument; unchecked and present removes it;
   unchanged writes nothing. A named manim colour that is unchanged keeps its name.

## Consequences

- Every mobject class and parameter manimlib exposes is editable without code changes
  here. New manimlib releases refresh the schema through the cache key.
- The probe costs a few seconds once per manimlib version. The first launch after an
  upgrade shows gears a few seconds late.
- Parameters such as `font_size` exist only as constructor arguments in manimlib 1.7.2
  (there is no `set_font_size`), and the panel shows them as constructor fields.
- Positional arguments bind to parameter names through the signature, so
  `Text("hi")` shows `text = "hi"`.
- "Add call" picks any chainable method from the schema and writes it as one
  `.method(...)` appended to the assignment. "Set in code only" hides the rows the code
  does not set.
- Not in this version: per-class collapsible sections, `t2c`/`t2g` colour-map rows,
  `Vect3` x/y/z fields (vectors show as expressions), user subclasses that resolve to a
  manimlib base only through the same file, palette sources in the colour field, inline
  constructors in `play()`, and multi-line expressions. Each extends the same schema and
  edit model.
- `api_index`, `color_sites` and the colour table remain for completion, the fingerprint
  and the offline fallback respectively.
