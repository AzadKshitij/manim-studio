---
title: "How I built a realtime editor for ManimGL in five days"
published: false
description: "I make Manim animations for YouTube. The tools that existed were too slow and nothing like editing, so I built Manim Studio: five days, 124 commits, three releases. This is the story. Part 2 goes inside the architecture."
tags: manim, python, opensource, productivity
cover_image: ""
series: "Building Manim Studio"
# Hashnode: rename `description` to `subtitle`, drop `series` and mention it in the intro instead.
---

> **TL;DR** — I built **Manim Studio**, a realtime desktop editor for
> [ManimGL](https://github.com/3b1b/manim) scenes: code on the left, a *live* preview on
> the right, a scrubbable timeline below. No render-wait-tweak loop — save the file and the
> preview updates in place; retime an animation by dragging it. It went from an empty
> project to **v1.1.0 in five days and 124 commits**. This is Part 1: why, what, and how it
> went. [Part 2](part-2-inside-manim-studio.md) is the technical deep-dive — the
> architecture and every choice that shaped it.

## The itch

Manim makes beautiful math animations, and I make them for my YouTube channel. The trouble
is the *making*. My loop was always the same: write a scene, wait for the render, watch the
result, tweak one number, wait again. Slow — and nothing about it felt like editing.

I tried the tools that already existed. The [Manim Community](https://www.manim.community/)
version. VS Code extensions driving Jupyter notebooks. Neither stuck. The iteration speed
was the killer, and Jupyter never felt like an editor — you're running cells, not cutting
video.

So I stopped looking and started building: something **faster**, that **feels like a video
editor**.

That single sentence turned out to be a design brief for everything that followed. "Faster"
meant never rendering a video just to see a change. "Feels like a video editor" meant a
timeline you scrub, a playhead, and retiming an animation by dragging its edge — not
editing a `run_time=` literal and re-rendering.

## The premise

Before writing code I wrote down what I actually wanted, and gave myself three
non-negotiables:

- **Realtime preview.** A persistent OpenGL context, updated at 60 fps — *no ffmpeg encode
  per edit*.
- **Production-grade, no hacks.** Fully tested. If a decision had to bend, it got written
  down. That turned into an [`AGENTS.md`](https://github.com/AzadKshitij/manim-studio/blob/main/AGENTS.md)
  contract and nine architecture decision records.
- **Drivable by an agent.** I wanted to be able to say "build me a CircleToSquare scene"
  and have a CLI do it. Everything in the GUI is also a command.

The shape of the app is almost boringly simple:

> 🖼️ **[Screenshot placeholder — landing view]**
> *The editor: code on the left, live preview on the right, timeline below.*
> Suggested file: `docs/assets/screenshots/editor.png`.

Left is a code editor. Right is the running scene. Bottom is the timeline. Everything else
is in service of keeping those three in sync.

## What it actually does

Concretely, here's the app in one screen:

- **A real code editor.** Scintilla, not a textbox: `manimlib` autocomplete and call
  signatures, error markers that jump to the failing line, syntax highlighting, full
  undo/redo.
- **A live preview** — embedded in the window by default, or a separate GL window — plus a
  live IPython shell wired to the running scene.
- **A timeline built from a runtime trace** of `play()` and `wait()`, so loops and branches
  produce the right blocks. Drag to scrub the live scene, wheel to pan, `Ctrl`+wheel to zoom,
  arrow keys to step one frame.
- **An editable timeline.** Retime or delete an animation by dragging it or typing an exact
  duration. The edit is anchored to the *code*, saved next to your scene, and baked into the
  export.
- **One-click export** to MP4 (up to 4K/60), GIF, or a still — through headless
  `manimgl -w`, honouring your timeline edits.
- **A CLI for everything.** Every feature is also a command, so the editor can be driven by
  an agent.

That last point mattered more than I expected. Because the GUI only ever talks to the
preview host through a message protocol, the exact same commands are available from a
terminal — `check`, `trace`, `export`, `new`, `preview`. I can say "build me a
CircleToSquare scene" and the tooling can actually do it.

## The trajectory

Five days, three shippable states:

| Release | Date | What landed |
| --- | --- | --- |
| **0.1.0** — initial shareable build | Oct 6 | Scintilla editor, embedded realtime preview, runtime-traced timeline with replay-based scrubbing and a filmstrip cache, live IPython shell, video/still export, and the headless CLI |
| **1.0.0** — first public release | Oct 7 | Pygments syntax highlighting, Jedi autocomplete + calltips, app icon, live memory/RAM status, and a one-click **frozen Windows build** (portable zip + per-user installer) |
| **1.1.0** | Oct 10 | The **editable timeline**: retime and delete animations as anchored edits stored beside the scene, baked into exports. Themes. A big rendering-performance pass |

The shape of that history is worth noticing: 0.1.0 is *"it works."* 1.0.0 is *"someone else
can install it."* 1.1.0 is the release where it stops being a preview and becomes an
**editor** — you don't just watch the timeline, you work on it.

> 🖼️ **[Screenshot placeholder — timeline]**
> *Lanes per animation, a block inspector, scrub, and the orphan tray.*
> Suggested file: `docs/assets/screenshots/timeline.png`.

## How it actually went

A few habits made five days possible. Every decision that bent the design got written down
as an ADR the moment I made it — nine of them, in order, from "why a separate process" to
"how to reuse cached frames after an edit." I kept the test suite green before every commit
(by the first release: **474 passing tests**), and I optimised nothing without a benchmark
to prove the change. That sounds like overhead. It isn't: when you're moving this fast, the
written decisions are what stop you from re-litigating the same question three times, and
the tests are what let you refactor a 1,000-line file into eight without breaking it.

## The three results I'm proudest of

If the whole thing has to justify itself in three sentences:

1. **A live preview with no per-edit re-render.** Save the file and the picture changes.
   Edits show a new frame in about **70 ms** — down from ~126 ms before the performance
   pass, and a different universe from a full render.
2. **Scrubbing that feels like video.** Drag the playhead and the scene follows, correctly,
   even with `always_redraw` and updaters. Under the hood it's replaying the scene 10–50 ms
   per step and caching frames so revisits are free.
3. **An editable timeline that survives the round trip.** Retime or delete an animation
   from the timeline; the edit is anchored to the *code*, survives unrelated edits, and is
   baked into the exported video — so what you see is what ships.

Those three are the whole reason the project exists. Everything in Part 2 is how they were
built.

## One more thing worth mentioning

The product didn't start out called Manim Studio — it began as "Manim Editor" and was
renamed to **Manim Studio** on the way to the first public release: import package
`manim_studio`, CLI `manim-studio`, Windows bundle `ManimStudio`. "Studio" describes what it
grew into: not a text editor that *also* shows you a preview, but an environment you work
in. The same stretch of work made the repository real — an MIT license, a CI pipeline
(format, lint, types, and tests on Ubuntu and Windows), a tagged release workflow,
Dependabot, and the usual community files. *"It works on my machine"* became *"it works on
yours."*

## What Part 2 covers

Part 1 was the story. [Part 2](part-2-inside-manim-studio.md) is the engineering, and it's
where the interesting decisions live:

- Why the preview runs in a **separate process** with the only OpenGL context, and why the
  editor never imports `manimlib`.
- Why the timeline is a **runtime trace**, not a parsed AST.
- Scrubbing, **three designs deep** — and why the obvious one doesn't work.
- How "make it fast" became a **measured discipline** with before/after numbers.
- How the timeline became an editor through **anchored patches in a sidecar file**.
- The long tail: LaTeX on demand, frozen packaging, themes-as-data — and the gotchas that
  cost real hours.

👉 **[Read Part 2: Inside Manim Studio](part-2-inside-manim-studio.md)**

If you want to follow along, the source is at
[github.com/AzadKshitij/manim-studio](https://github.com/AzadKshitij/manim-studio).