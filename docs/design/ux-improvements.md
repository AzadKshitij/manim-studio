# UX improvement plan

Status: proposed · Owners: editor team · Last updated: 2026-10-11

This is the design of record for the "make it feel better" pass over the desktop
editor. It is split into three independently shippable phases. Each phase is a
single-purpose change set with offscreen tests; GL-only behaviour is covered by
the existing `@pytest.mark.gl` suites.

The guiding principles, in priority order:

1. **Never look broken.** An empty state must read as "ready", not "stalled".
2. **Show, don't hide.** Feedback must be persistent enough to notice, and
   discoverable without reading tooltips.
3. **Continuity.** Reopening the app should feel like resuming, not restarting.
4. **Stay out of the way.** Nothing should steal space or focus unasked.

---

## Findings (what feels rough today)

Grounded in the current code (`main_window.py`, `timeline_widget.py`,
`preview_panel.py`, `shell_panel.py`, `timeline_transport.py`, `styling.py`):

| # | Friction | Evidence |
| - | -------- | -------- |
| F1 | Cold start is empty | `manim-studio gui` with no file opens `untitled`; `_start_preview` returns when `_path is None`; `scenes/Welcome.py` is never opened |
| F2 | Actions that need a scene are enabled but silently no-op | `request_play` → `_start_preview` returns; Render/Export only set a status line |
| F3 | The Live shell steals vertical space | `ShellPanel.append` calls `self.show()` on every line, including routine host logs |
| F4 | Status is split four ways and mostly transient | `statusBar().showMessage()`, preview header, transport `edits_label`, status-bar `preview: off` |
| F5 | Preview is passive | `PreviewPanel` "renders frames and shows status"; no click/scroll, no staleness cue |
| F6 | Timeline lanes have no legend; snapping has no on-canvas indicator | `_draw_lanes`, `View > Snap timeline` |
| F7 | Rich timeline interactions are tooltip-only | Shift-drag ruler, Ctrl+double-click, double-click-to-retime |
| F8 | No editor find/replace, no go-to-line, no outline | no search wiring in `code_editor.py` |
| F9 | No command palette or shortcuts reference | menus only |
| F10 | Disk conflict is a status line, not a decision | `_on_file_changed` prints "save to overwrite, or reopen" |
| F11 | Continuity is limited to window geometry | `_restore_geometry` saves only `window/geometry`; no splitters, dock, last file or recent list |
| F12 | Only one built-in theme; fixed preview size; no editor zoom | `BUILTIN_THEMES` (Chalkboard), `preview_size` arg, no font-size action |

---

## Phase 1 — Cold start, chrome & discoverability

Goal: launching with nothing open feels intentional, and every action that
cannot run says so.

### 1.1 Welcome surface
- New `editor/welcome_panel.py`: a `WelcomePanel` shown in place of the preview
  when no scene is open. Contains:
  - **Open scene…** and **New scene…** buttons (emit signals; the window drives
    the existing dialogs).
  - **Recent files** list (click to open), empty-state text when there are none.
  - A short "first steps" hint: edit → save → preview reloads; drag the timeline
    to scrub.
- The right pane of the horizontal splitter becomes a `QStackedWidget`
  (`preview_panel` + `welcome_panel`); the window switches pages on
  `open_path`/close-to-empty. `PreviewPanel` stays untouched.

### 1.2 Recent files & last file
- `recent/files` (`QSettings`, list, newest first, de-duplicated, capped at 8),
  updated on every successful `open_path`, pruned of missing paths on read.
- Remembered only when `restore_settings` is true (tests and scripted windows
  stay isolated, matching the theme behaviour).
- On startup with no explicit path, reopen the last file when
  `window/restore_last_file` is on; otherwise show the welcome page.

### 1.3 Honest action states
- Group the scene-dependent actions (Play, Reload, Stop, Render preview,
  Export…, Export still…) and enable them only when a scene is open; add the
  tooltip "Open a scene first" while disabled. A single `_update_scene_actions()`
  is called from `open_path`, `save_buffer_as` and the welcome page.

### 1.4 Live shell without the ambush
- `ShellPanel.append` no longer calls `show()`; it increments an unread counter
  and emits `unreadChanged(int)`.
- A status-bar indicator ("Live shell · 3") reveals the dock on click and resets
  the counter; `focus_prompt()` clears it. Errors and failed exports call
  `reveal()` explicitly, so real problems still surface.

### 1.5 Discoverability
- **Help → Keyboard shortcuts…** dialog listing every `QAction` with a shortcut,
  grouped by menu and sorted, rendered from the live action set (no hand
  maintenance).
- Timeline hint strip (one line under the ruler, dismissible) summarising
  Shift-drag = range, double-click = duration, Ctrl+double-click = code.

Acceptance: launching with no file shows the welcome page and disabled transport;
opening a scene enables it; recents survive a restart; a host log line no longer
opens the dock.

---

## Phase 2 — Preview & timeline feel

Goal: the preview reacts to the pointer and the timeline shows you what it knows.

### 2.1 Interactive preview
- `PreviewPanel` gains opt-in pointer behaviour that emits signals (kept dumb):
  - click = play/pause, wheel = step one frame, double-click = fit/reset.
- A **stale-preview pill** appears when the buffer is modified while the host is
  running ("Preview out of date — save (Ctrl+S)"), hidden otherwise.

### 2.2 Hover-scrub thumbnails
- While hovering/dragging the ruler, show a small thumbnail from the filmstrip
  cache plus the block name and time. Cache-first; falls back to no thumbnail
  (never a replay) when the frame is missing, so dragging stays cheap.

### 2.3 Timeline legibility
- A collapsible **lane legend** (colour → animation) in the corner of the strip.
- An on-canvas **snap indicator** next to the zoom buttons that reflects
  `View > Snap timeline`.
- Keep block labels readable when zoomed out (a minimum label box or a
  playhead-following block-name chip).

Acceptance: clicking the preview plays; editing the buffer raises the pill;
hovering the ruler shows a thumbnail when cached; the legend names the lanes.

---

## Phase 3 — Editor & continuity

Goal: the everyday editing loop and reopening the app both stop costing clicks.

### 3.1 Editor ergonomics
- **Find/Replace bar** over Scintilla's search target: `Ctrl+F`, `Ctrl+H`,
  `F3`/`Shift+F3`, Enter to find next, Esc to close.
- **Go to line** (`Ctrl+G`).
- **Outline** of `play()`/`wait()` call sites (from the trace's `call_sites`)
  in the shell dock's sibling or a small panel; click jumps to code.

### 3.2 Safe conflict resolution
- Replace the file-watcher status line with a chooser bar:
  **Keep mine / Take theirs / Show diff**, wired to `EditsDiffDialog` for the
  diff view and `note_save()` for "keep mine".

### 3.3 Continuity
- Persist and restore `QSplitter.saveState()` for both splitters, the Live
  shell dock visibility, `window/embedded`, and the selected scene per file.
- Design decision: splitter/dock state is stored under `window/` in `QSettings`
  and restored only with `restore_settings`.

### 3.4 Comfort (stretch)
- Ship a light built-in theme alongside Chalkboard.
- Editor font-size zoom (`Ctrl+=` / `Ctrl+-` / `Ctrl+0`).
- A preview resolution/quality selector (replacing the fixed `preview_size`).

Acceptance: `Ctrl+F` finds text; a conflicting disk write prompts a decision;
relaunch restores splits, dock, mode and last file.

---

## Out of scope

- Multi-file tabs and a project explorer.
- A full command palette (a natural follow-up to 1.5; deferred to keep the
  first pass small).
- Protocol changes: none of the above alter `PROTOCOL_VERSION`.

## Testing

- Pure decision logic (recents, conflict policy, outline extraction) lives in
  pure modules and is unit tested without Qt.
- Each Qt surface gets an offscreen smoke test (`QT_QPA_PLATFORM=offscreen`,
  `pytest-qt`) in `tests/test_editor_smoke.py` or a dedicated file.
- No fixed sleeps; timers use injection where relevant.
