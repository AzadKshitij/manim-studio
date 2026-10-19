# Make Manim animations with an AI agent

This guide is for people who got **Manim Studio** as a portable folder and want an AI
assistant to write, check and export ManimGL animations for them.

Manim Studio does not contain an AI. It gives your agent a set of tools: check a scene,
run it, look at its frames, and export a video. You bring the agent. Any of these works:
Claude Code, Cursor, Codex, or opencode, with your own account.

---

## 1. What you need

| | Required? | Why |
|---|---|---|
| Manim Studio (the portable folder) | Yes | Runs the scenes and the tools |
| An AI agent app (see section 3) | Yes | This is the assistant that writes the animations |
| ffmpeg | For video only | Turns frames into an MP4/GIF. Frames and checks work without it |
| LaTeX | Only for formulas | Needed when a scene uses `Tex` or `MathTex`. Installed on demand |
| Internet | Only for installing ffmpeg or LaTeX | Everything else runs offline |

Windows is the supported platform for the portable build.

---

## 2. Set up Manim Studio once

1. **Unzip** the portable folder somewhere with a short path and no spaces, for
   example `C:\Tools\ManimStudio`. You will get `ManimStudio\manim-studio.exe`.
   Remember this path. Below, `<STUDIO>` means the full path to `manim-studio.exe`,
   for example `C:\Tools\ManimStudio\ManimStudio\manim-studio.exe`.

2. **Install ffmpeg** (only needed to export videos). Open a terminal and run:

   ```bash
   "<STUDIO>" ffmpeg status
   ```

   If it says `not found`, install it:

   ```bash
   "<STUDIO>" ffmpeg install --yes
   ```

   This downloads about 110 MB from the official ffmpeg publisher, checks it against the
   publisher's checksum, and installs it only for your user account. It needs internet
   access once.

3. **Check the app works**:

   ```bash
   "<STUDIO>" check --help
   ```

   You should see the usage text, not an error.

---

## 3. Install an AI agent app

Use whichever you already have. Each one needs its own account or subscription.

- **Claude Code** (command-line app from Anthropic)
- **Cursor** (code editor with an AI assistant)
- **Codex** (OpenAI's command-line agent)
- **opencode** (open-source command-line agent)

---

## 4. Connect the agent to your project

Create a folder for your animations, for example `C:\Animations\MyFirstVideo`. Then in
that folder run:

```bash
"<STUDIO>" agent-init . --command "<STUDIO>"
```

Quote the path if it contains spaces. Add `--dry-run` first to see what would change:

```bash
"<STUDIO>" agent-init . --command "<STUDIO>" --dry-run
```

`agent-init` writes these files, and it never overwrites a file you have edited:

| File | Used by |
|---|---|
| `.claude/skills/manim-scene-authoring/SKILL.md` | Claude Code: how to write ManimGL scenes with these tools |
| `.cursor/rules/manim-studio.mdc` | Cursor: the same guidance |
| `AGENTS.md` (a clearly marked section) | Any agent that reads `AGENTS.md` |
| `.mcp.json` | Claude Code: connects to the Manim Studio tools |
| `.cursor/mcp.json` | Cursor: connects to the Manim Studio tools |
| `opencode.json` | opencode: connects to the Manim Studio tools |

Then connect your agent:

- **Claude Code:** open the project folder in a terminal, run `claude`, and approve the
  `manim-studio` server if it asks. Type `/mcp` inside Claude Code to see it listed.
- **Cursor:** open the project folder. In Cursor's MCP settings, `manim-studio` should show
  as connected. If not, reload the window.
- **opencode:** open the project folder and start opencode. It reads `opencode.json`.
- **Codex:** Codex keeps its server list for your user account, so run this once:

  ```bash
  codex mcp add manim-studio -- "<STUDIO>" mcp --root .
  ```

---

## 5. Make your first animation

Open your agent in the project folder and ask in plain words. For example:

> Make a 3-second animation: a blue circle appears, turns into a yellow square, and
> fades out. Export it as a video.

Behind the scenes the agent should:

1. Write a scene file (`new_scene` or `write_scene`).
2. Check it for errors (`check`).
3. Run it and confirm the timing (`trace`).
4. Look at the whole animation as one image (`contact_sheet`).
5. Look at one moment in detail if something looks wrong (`frame`).
6. Export the video (`export`), which is written next to the scene file.

You do not need to know ManimGL. Ask for changes in plain words too: "make it slower",
"move the square to the left", "use the colour #ff8800 for the text".

---

## 6. Watch the results

- **Contact sheet:** one picture of many moments, each labelled with its time and the
  animation step it belongs to. Look for empty frames, objects that never move, or
  objects leaving the screen.
- **Video:** the MP4 appears next to your scene file. Open it like any video.
- **Editor (optional):** you can open a scene in the Manim Studio editor to watch it
  play and to adjust timing by hand. The agent and the editor can work on the same
  file.

---

## 7. Good prompts

- "Explain the scene you wrote, step by step, with the timing."
- "Show me a contact sheet, then fix any frame where nothing is visible."
- "Export a low-quality preview first, so I can check it quickly."
- "Export a high-quality MP4 at 60 fps."
- "Make an animated GIF of the first 3 seconds."
- "Use manim-studio to list the templates, and start from the circle one."

---

## 8. When something goes wrong

| What you see | What to do |
|---|---|
| The agent says it cannot find the tools | Check the server is connected (section 4). Run `"<STUDIO>" agent-init . --command "<STUDIO>"` again |
| Export says ffmpeg was not found | Run `"<STUDIO>" ffmpeg install --yes`, then ask the agent to export again |
| A formula does not render (`Tex` or `MathTex`) | Run `"<STUDIO>" latex install`, then ask again. This needs internet once |
| The agent writes Manim Community code (`Create`) | Ask it to use ManimGL names (`ShowCreation`). The guide it reads says so |
| A file the agent should not touch was changed | Manim Studio only reads and writes files inside the folder you started the agent in |
| Something else | Run `"<STUDIO>" check <scene.py>` on the scene file and send the message it prints |

---

## 9. Where your files are

- Your animations: the folder you ran `agent-init` in. Scene files are `.py` files there.
- Videos the agent exports: next to the scene file. Images the agent makes for you go to
  a temporary folder, and the agent tells you the path.
- ffmpeg, if you installed it: `%LOCALAPPDATA%\manim-studio\ffmpeg`. Delete that folder
  to remove it.

Manim Studio itself only downloads things you ask for: ffmpeg and LaTeX. Its preview talks
to your own computer only. Your agent app is different: it sends your prompts, and any
files it reads, to its own service, under that service's terms.
