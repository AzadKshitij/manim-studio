---
title: "How I built a realtime editor for ManimGL"
published: false
description: "I make Manim animations for YouTube. The tools that existed were too slow and nothing like editing, so I built Manim Studio. This is the story; Part 2 goes inside the architecture."
tags: manim, python, opensource, productivity
cover_image: ""
series: "Building Manim Studio"
# Hashnode: rename `description` to `subtitle`, drop `series` and mention it in the intro instead.
---

> **TL;DR:** With AI helping write the code, we built **Manim Studio**, a realtime desktop editor for [ManimGL](https://github.com/3b1b/manim) scenes: code on the left, a *live* preview on the right, a scrubbable timeline below. No render-wait-tweak loop: save the file and the preview updates in place; retime an animation by dragging it. It went from an empty project to **v1.1.0 in 124 commits**. This is Part 1: why it exists, what it does, and how it went. [Part 2](part-2-inside-manim-studio.md) is the technical deep-dive into the architecture and every choice that shaped it.

## The itch

Manim makes beautiful math animations, and I make them for my YouTube channel. The trouble is the *making*. My loop was always the same: write a scene, wait for the render, watch the result, tweak one number, wait again. It was slow, and nothing about it felt like editing.

I tried the tools that already existed. The [Manim Community](https://www.manim.community/) version. VS Code extensions driving Jupyter notebooks. Neither stuck. The iteration speed was the killer, and Jupyter never felt like an editor. It's a place to run code and inspect output, not a place to work on an animation.

So I stopped looking and started building: something **faster**, that **feels like a video editor**.

That single sentence turned out to be a design brief for everything that followed. "Faster" meant never rendering a video just to see a change. "Feels like a video editor" meant a timeline you scrub, a playhead, and retiming an animation by dragging its edge instead of editing a `run_time=` literal and re-rendering.

I didn't write all the code by hand. I used AI to help write it. When I say "we" in this post, I mean me and the AI working on the implementation. "I" still means my own experience, goals, and decisions.

## The premise

Before writing code I wrote down what I actually wanted, and gave myself three non-negotiables:

- **Realtime preview.** A persistent OpenGL context, updated at 30 fps, with no ffmpeg encode per edit.
- **Production-grade, no hacks.** Fully tested. Whenever a design decision changed course, I wrote down why. That habit became an [`AGENTS.md`](https://github.com/AzadKshitij/manim-studio/blob/main/AGENTS.md) contract and nine architecture decision records.
- **Drivable by an agent.** I wanted to be able to say "build me a CircleToSquare scene" and have a CLI do it. Everything in the GUI is also a command.

The shape of the app is almost boringly simple: a code editor on the left, the running scene on the right, the timeline below. Everything else exists to keep those three in sync.

![ManimStudio](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/557alu4k4rk065oxqard.png)

*The editor: code on the left, live preview on the right, timeline below.*

## What it actually does

Concretely, this is what's in the box:

- **A real code editor.** Scintilla, not a textbox: `manimlib` autocomplete and call signatures, error markers that jump to the failing line, syntax highlighting, full undo/redo.
- **A live preview**, embedded in the window by default or shown in a separate GL window, plus a live IPython shell wired to the running scene.
- **A timeline built from a runtime trace** of `play()` and `wait()`, so loops and branches produce the right blocks. Drag to scrub the live scene, wheel to pan, `Ctrl`+wheel to zoom, arrow keys to step one frame.
- **An editable timeline.** Retime or delete an animation by dragging it or typing an exact duration. The edit is anchored to the *code*, saved next to your scene, and baked into the export.
- **One-click export** to MP4 (up to 4K/60), GIF, or a still, using headless `manimgl -w` and honouring your timeline edits.
- **A CLI for everything.** Every feature is also a command, so the editor can be driven by an agent.

That last point mattered more than I expected. Because the GUI only ever talks to the preview host through a message protocol, the exact same commands are available from a terminal: `check`, `trace`, `export`, `new`, and `preview`. I can say "build me a CircleToSquare scene" and the tooling can actually do it.

## The trajectory

Three shippable states, each with a job to do:

| Release | What landed |
| --- | --- |
| **0.1.0: initial shareable build** | Scintilla editor, embedded realtime preview, runtime-traced timeline with replay-based scrubbing and a filmstrip cache, live IPython shell, video/still export, and the headless CLI |
| **1.0.0: first public release** | Pygments syntax highlighting, Jedi autocomplete + calltips, app icon, live memory/RAM status, and a one-click **frozen Windows build** (portable zip + per-user installer) |
| **1.1.0** | The **editable timeline**: retime and delete animations as anchored edits stored beside the scene, baked into exports. Themes. A big rendering-performance pass |

0.1.0 is *"it works."* 1.0.0 is *"someone else can install it."* 1.1.0 is where it stops being a preview and becomes an **editor**: you don't just watch the timeline, you work on it.

![Timeline Editor](https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/wuathnuc01cxja49sjbi.png)

*Lanes per animation, a block inspector, scrub, and the orphan tray.*

## How it actually went

A few habits kept the project honest as it grew. Every decision that bent the design got written down as an ADR the moment I made it: nine of them, in order, from "why a separate process" to "how to reuse cached frames after an edit." We kept the test suite green before every commit (by the first release: **474 passing tests**), and we optimised nothing without a benchmark to prove the change.

It wasn't smooth, though. Scrubbing took **three designs** to get right, and the obvious one didn't work.

<!-- TODO: add one or two sentences on what went wrong with the first attempt and what it cost you. This is the most relatable part of the post. -->

The written decisions are what stopped me re-litigating the same question three times, and the tests are what let us refactor a 1,000-line file into eight without breaking it.

## Becoming Manim Studio

The product began as "Manim Editor" and was renamed Manim Studio on the way to the first public release: import package `manim_studio`, CLI `manim-studio`, Windows bundle `ManimStudio`. "Studio" describes what it grew into: not a text editor that also shows you a preview, but an environment you work in. The same stretch of work made the repository real: an MIT license, a CI pipeline (format, lint, types, and tests, currently on Windows), a tagged release workflow, Dependabot, and the usual community files. "It works on my machine" became "it works on yours."

## Try it

Manim Studio is MIT-licensed and open source. The packaged build is for **Windows** (a portable zip or a per-user installer); CI currently runs on Windows too. Grab it from the [Releases page](https://github.com/AzadKshitij/manim-studio/releases), or clone the repo from [github.com/AzadKshitij/manim-studio](https://github.com/AzadKshitij/manim-studio). If you make Manim animations, I'd love to hear what's missing. Issues, feedback, and stars are all welcome.

## What Part 2 covers

Part 1 was the story. Part 2 is the engineering, and it's where the interesting decisions live:

- Why the preview runs in a **separate process** that owns the only OpenGL context, and why the editor's GUI never touches GL.
- Why the timeline is a **runtime trace**, not a parsed AST.
- Scrubbing, **three designs deep**, and why the obvious one doesn't work.
- How "make it fast" became a **measured discipline** with before/after numbers.
- How the timeline became an editor through **anchored patches in a sidecar file**.
- The long tail: LaTeX on demand, frozen packaging, themes-as-data, and the gotchas that cost real hours.