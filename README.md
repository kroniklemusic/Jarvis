# JARVIS

A voice assistant for your Mac. Say **"hey Jarvis"**, ask for something out loud, and JARVIS
answers out loud. It can search the web, browse websites and control apps on your Mac. It always
asks you first before it does anything that changes, sends or deletes something.

The "brain" is Claude Code, running on your Mac under your existing **Claude Pro or Max**
subscription. JARVIS never uses an API key, so there are no per-request charges. Everything else
(wake word, speech recognition, voices and tools) is free and open source. The speech parts run
entirely on your Mac.

---

## Contents

1. [What you need](#what-you-need)
2. [One-time setup](#one-time-setup)
3. [Daily use: start, talk, stop](#daily-use)
4. [What JARVIS can do, and how it asks permission](#what-jarvis-can-do)
5. [Memory](#memory)
6. [Start automatically at login](#start-automatically-at-login)
7. [Mac permissions (microphone, Automation, ...)](#mac-permissions)
8. [Changing the voice and other settings](#changing-settings)
9. [Optional: Gmail, Calendar and Google Drive](#optional-google-workspace)
10. [Health check](#health-check)
11. [Troubleshooting](#troubleshooting)
12. [Updating and removing JARVIS](#updating-and-removing)
13. [Logs and privacy](#logs-and-privacy)

---

## What you need

- A Mac (Apple Silicon or Intel) with macOS 13 Ventura or newer, a microphone and speakers.
- A Claude Pro or Max subscription.
- About 8 GB of free disk space, and an internet connection for setup.

## One-time setup

You'll type a few commands into the **Terminal** app. To open it, press Command + Space, type
`Terminal` and press Return. Paste each grey box below, one at a time, and press Return.

**1. Install Homebrew** (a free installer for developer tools), if you don't have it already.
Go to <https://brew.sh>, copy the install line shown there, paste it into Terminal and follow its
instructions. At the end it may show two or three extra lines to run. Run those too.

**2. Install Claude Code and sign in:**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
Close Terminal and open a **new** window. Type `claude`, press Return, and sign in with your
Claude Pro or Max account. When you see the chat prompt, type `/exit`.

**3. Download JARVIS:**
```bash
git clone https://github.com/kroniklemusic/jarvis.git ~/jarvis
```
If a box appears asking to install "command line developer tools", click **Install**, wait until it
finishes, then paste the line again.

**4. Run setup:**
```bash
cd ~/jarvis
```
```bash
./setup.sh
```

Setup takes 15 to 40 minutes the first time, mostly downloading. It will:
- install the helper tools (ffmpeg, portaudio, uv, Node);
- make a private Python environment inside the `jarvis` folder;
- download the wake word, speech recognition, voice and web-browser files;
- **open Claude Code once** so you can trust the JARVIS folder. When asked, choose
  **"Yes, I trust this folder"**, then type `/exit`;
- run the [health check](#health-check). You'll hear a test sentence;
- build the **JARVIS app** and put it in your Applications folder (this uses Apple's free
  command line tools, which you already have if `git` works);
- ask whether JARVIS should start automatically at login.

You can run `./setup.sh` again at any time. It skips whatever is already done.

## Daily use

**Start JARVIS.** Open the **JARVIS** app like any other app: press Command + Space, type `JARVIS`,
and press Return. It's also in your Applications folder and Launchpad. To keep it in the Dock,
right-click its Dock icon, then choose **Options → Keep in Dock**.

The JARVIS window shows a boot screen while it loads (10 to 30 seconds). It's ready when you hear
**"At your service."**

**Quit JARVIS:** press **Command + Q**, or close the window.

### The JARVIS display

![The JARVIS display](docs/hud.jpg)

**The holographic core** in the middle is a live 3D arc reactor, projected above a holographic floor.
It reacts to your voice and to JARVIS's, and its colour shows what JARVIS is doing:

| Colour | Meaning |
|---|---|
| Calm blue | **Standing by** |
| Bright blue, particles flowing in | **Listening** |
| Violet | **Transcribing** your words |
| Amber, spinning fast | **Processing** with Claude |
| Pulsing blue | **Responding** |
| Red | Waiting for your **authorisation** |
| Grey | Microphone **off** |

Click the core to talk.

**Comms** (left panel) is the conversation:
- JARVIS's replies type out as it speaks. Hover over one to **Copy** it.
- Each tool JARVIS uses shows a spinner, then a tick when it's done. Small timings under each answer
  show how long it took.
- **New** starts a fresh conversation.

**Diagnostics** (right panel) has live readings from your Mac:
- dials for processor, memory and battery;
- a network speed graph and free disk space;
- live microphone and voice levels;
- which Claude model, voice and speech model are in use;
- status lights for the wake word and the Claude connection;
- the tools JARVIS can use. Each one lights up while it's in use.

**The command bar** (bottom):
- **Type** a request and press Return. It's handled exactly like a spoken one, and is handy in a
  noisy room.
- **Quick actions** above it ask common questions in one click: weather, your day, headlines,
  unread email.
- **Talk** (the microphone, or **Space**) works like saying "hey Jarvis".
- **Stop** (**Esc**) cancels whatever JARVIS is doing.

**The switches** at the top:
- **Mic live / Mic off** turns listening off completely (pressing Talk turns it back on).
- **Voice on / Voice off** makes JARVIS answer silently, on screen only.

**Settings** (the gear icon, or press **,**) changes these straight away and saves them to
`config.yaml`:
- the Claude model (Haiku, Sonnet or Opus);
- the voice and speaking speed;
- wake-word sensitivity and the follow-up window;
- whether timings are shown;
- **Lite** effects, if your Mac feels sluggish. Macs with 8 GB of memory start in Lite.

**Authorisation Required**: when JARVIS needs your OK, a red panel appears, with a countdown.
Click **Authorise** or **Deny**, press **Y** or **N**, or say "yes, proceed" or "no". If you're in
another app, JARVIS's Dock icon bounces to get your attention.

**Keyboard:**
- **Space**: talk
- **Esc**: stop
- **/**: type a command
- **,**: open Settings
- **Y** / **N**: answer an authorisation request
- **Control + Command + F**: full screen

**Without the app:** you can also run JARVIS in Terminal, with plain text instead of the display:
```bash
cd ~/jarvis && .venv/bin/python -m jarvis
```
Stop it with Control + C.

**Talk to it:**
- Say **"hey Jarvis"**. A soft *tink* means it's listening. Ask your question normally.
- When you stop talking, JARVIS answers out loud. It starts speaking before it has finished thinking.
- **Follow-ups:** for 8 seconds after it finishes, just keep talking. No need to say "hey Jarvis" again.
- **Interrupt:** say "hey Jarvis" while it's talking. It stops at once and listens.
- **Push-to-talk:** press **Control + Option + J** instead of saying "hey Jarvis".

Try saying:
- "Hey Jarvis, what's the weather in London tomorrow?"
- "Hey Jarvis, search the web for a good banana bread recipe."
- "Hey Jarvis, open Safari and go to bbc.co.uk."
- "Hey Jarvis, what's on my calendar today?" (uses the Calendar app)
- "Hey Jarvis, remember that my sister's name is Anna."

## What JARVIS can do

| Tool | What it's for | Does JARVIS ask first? |
|---|---|---|
| Web search (DuckDuckGo, and Claude's own search) | Answering questions with up-to-date facts | No. It only reads. |
| Web browser (Playwright, using an open-source Firefox) | Opening and reading websites | Opening and reading: no. **Clicking, typing, filling in forms: yes.** |
| Mac automation (AppleScript) | Controlling apps: Mail, Calendar, Reminders, Music, Finder, Safari... | **Yes, every time.** |
| Files and commands | Reading files, running commands | Reading: no. **Changing files or running commands: yes.** |
| Memory (`memory/` folder) | Remembering facts about you | No |

Some things JARVIS will **never** do. It's blocked from reading passwords, `.env` files, SSH keys or
your keychain, and from running `curl`, `wget`, `sudo` or `rm -rf`. Commands run inside the macOS sandbox.

### How permission works

When Claude needs your OK, JARVIS tells you what it wants to do and asks:

> "I'd like to run an AppleScript that controls apps on your Mac. Shall I proceed? Say yes, proceed, or no."

You'll hear a *tink*. Then:
- Say **"yes, proceed"** (or "yes", "go ahead") to allow it.
- Say **"no"** (or "stop", "cancel") to refuse.
- If JARVIS can't understand you, it asks once more. Saying nothing for 15 seconds counts as **no**.
- If you say "hey Jarvis" during the question, that also counts as **no**.

Before it sends, deletes, buys or posts anything, JARVIS also says exactly what it's about to do and
waits for your yes. It treats text from websites and emails as information, never as instructions,
so a web page can't tell it what to do.

## Memory

JARVIS remembers lasting facts you tell it, such as names, preferences and routines. It keeps them
in **`memory/facts.md`**, a plain text file inside the `jarvis` folder.
- Say "remember that I take my coffee black" and it adds a line to that file.
- To check or correct what it knows, open `memory/facts.md` with TextEdit. You can edit or delete
  any line.
- It never stores passwords, card numbers or anything you call secret.
- Your memory file is never uploaded to GitHub. Setup tells git to leave it alone.

## Start automatically at login

To turn it **on** (setup also offers to do this):
```bash
~/jarvis/launchd/install-autostart.sh
```
After that, the JARVIS app opens each time you log in. Quit it with Command + Q to stop it for this
session. If the app hasn't been built, a Terminal window running JARVIS opens instead. If you build
the app later, run the command above again so autostart opens the app.

To turn it **off**:
```bash
~/jarvis/launchd/install-autostart.sh --remove
```

## Mac permissions

macOS asks for permission the first time JARVIS needs something. Click **Allow** or **OK**.
If you clicked "Don't Allow" by mistake, you can turn it back on yourself:

| Permission | Why JARVIS needs it | Where to turn it on |
|---|---|---|
| **Microphone** | To hear you | System Settings → Privacy & Security → **Microphone** → turn on **JARVIS** |
| **Automation** | To control apps (Mail, Calendar, Safari, ...) | System Settings → Privacy & Security → **Automation** → find **JARVIS** → turn on each app you want JARVIS to control |
| **Accessibility** | For the Control + Option + J shortcut, and for some app controls | System Settings → Privacy & Security → **Accessibility** → turn on **JARVIS** |
| **Input Monitoring** | For the Control + Option + J shortcut on some macOS versions | System Settings → Privacy & Security → **Input Monitoring** → turn on **JARVIS** |

After changing a permission, quit JARVIS (Command + Q) and open it again.
If you run JARVIS from Terminal instead of the app, give these permissions to **Terminal** (or to
iTerm, or whichever terminal app you use).

The first time JARVIS controls a new app, macOS shows "JARVIS wants access to control *App*".
Click **OK**. If you click "Don't Allow", turn it on again under **Automation**, as in the table.

The Space-bar Talk button inside the JARVIS window needs none of these. It always works.

## Changing settings

All settings are in **`config.yaml`** inside the `jarvis` folder. Open it with TextEdit (right-click
→ Open With → TextEdit), change a value, save, and restart JARVIS. Every setting has a note
explaining it. If you make a typo, JARVIS tells you which line is wrong when it starts.

### Changing the voice

- **Choose a different voice:** change `kokoro_voice`.
  - British male voices: `bm_george` (default), `bm_lewis`, `bm_daniel`, `bm_fable`.
  - British female voices: `bf_emma`, `bf_isabella`, `bf_alice`, `bf_lily`.
  - American voices also work, for example `am_michael`, `am_adam`, `af_heart`, `af_bella`.
- **Speak faster or slower:** change `speech_speed`. For example, `1.15` is a little faster and `0.9` a little slower.
- **Backup voices:** `tts_engines` is the order JARVIS tries its voice engines in (`kokoro`, then
  `piper`, then `say`, the Mac's built-in voice). If one fails, it moves to the next one by itself.
  `piper_voice` and `say_voice` choose those voices. If you change `piper_voice`, run `./setup.sh`
  again to download it.

### Other settings

| Setting | What it does |
|---|---|
| `model` | Which Claude model answers: `sonnet` (default), `opus` (smartest, slower) or `haiku` (fastest). |
| `wake_threshold` | Raise it (e.g. `0.7`) if JARVIS wakes up by accident. Lower it (e.g. `0.35`) if it doesn't hear you. |
| `hotkey` | The push-to-talk shortcut. |
| `follow_up_seconds` | How long it listens for a follow-up. `0` turns follow-ups off. |
| `end_of_speech_silence_ms` | How long a pause ends your request. Raise it to `1000` if JARVIS cuts you off. |
| `confirm_timeout_seconds` | How long it waits for "yes, proceed" or "no". |
| `chime` | `false` turns off the *tink* sound. |
| `show_timings` | After each answer, show a line like "(timing: understood your words in 0.6s, Claude's first words 1.9s, voice started 0.7s after the text)". `false` hides it. |
| `kokoro_device` | Where the Kokoro voice runs: `cpu` (the processor, default and recommended), `mps` (the Apple Silicon graphics chip) or `auto` (test both at startup and keep the faster one). |
| `auto_fast_voice` | If Kokoro is too slow on your Mac, JARVIS uses the faster Piper voice so replies start quickly. Set to `false` to always use Kokoro. |

## Optional: Google Workspace

JARVIS can also read and draft Gmail, and use Google Calendar, Drive and Docs. This is **off** by
default because it needs a one-time Google setup, which takes about 15 minutes:

1. Go to <https://console.cloud.google.com/>, sign in, and create a new project (any name, e.g. "jarvis").
2. Open **APIs & Services → Library**. Enable the **Gmail API**, **Google Calendar API**,
   **Google Drive API** and **Google Docs API**.
3. Open **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and
   your email, and under **Test users** add your own Gmail address.
4. Open **APIs & Services → Credentials → Create credentials → OAuth client ID**. Choose
   **Desktop app** as the type, then click Create. Keep the **Client ID** and **Client secret** handy.
5. In Terminal, run `touch ~/.zprofile && open -e ~/.zprofile` and add these two lines at the bottom,
   with your own values between the quotes. Then save and close the file. The JARVIS app reads this
   file when it starts.
   ```
   export GOOGLE_OAUTH_CLIENT_ID="paste-client-id-here"
   export GOOGLE_OAUTH_CLIENT_SECRET="paste-client-secret-here"
   ```
6. Open `.claude/settings.json` in the `jarvis` folder with TextEdit. It's hidden, so in Finder press
   Command + Shift + . to show hidden files. Move `"google-workspace"` from the
   `disabledMcpjsonServers` list to the `enabledMcpjsonServers` list.
7. Quit JARVIS (Command + Q) and open it again. The first time you ask about email or your calendar,
   a browser window opens so you can sign in to Google and allow access.

JARVIS asks your permission ("Shall I proceed?") before **every** Google Workspace action, including reading.

## Health check

If something seems wrong, run:
```bash
cd ~/jarvis && ./doctor.sh
```
It checks each of these and tells you how to fix anything that fails:
- Claude Code is installed and you're signed in.
- No `ANTHROPIC_API_KEY` is set.
- The JARVIS folder is trusted.
- The microphone works.
- Each tool server starts.
- The voice speaks a test line.

## Troubleshooting

| What you see or hear | What to do |
|---|---|
| "I can't use the microphone" | Turn on **JARVIS** (or Terminal, if you run it from Terminal) under System Settings → Privacy & Security → Microphone, then quit and reopen JARVIS. |
| Spotlight or Launchpad shows **two** JARVIS apps | Older versions left a second copy inside the jarvis folder. Run `cd ~/jarvis && git pull && ./app/build-app.sh`. It removes the extra copy and keeps a single JARVIS in Applications. |
| The display feels slow or your Mac's fans spin up | Open Settings (gear icon) → Display → **Lite**. |
| JARVIS doesn't appear in Applications / setup says "The app couldn't be built" | Install Apple's command line tools with `xcode-select --install`, then run `./app/build-app.sh`. Until then, start JARVIS from Terminal with `.venv/bin/python -m jarvis`. |
| The JARVIS window says "JARVIS can't continue" | The message says what's wrong. Click **Open Logs Folder** for details (`app.log` and `jarvis.log`), or run `./doctor.sh`. |
| The JARVIS window stays on "Starting engine" | Wait up to a minute the first time. If it never changes, quit, then run `./doctor.sh` in Terminal. |
| The display looks frozen or blank | Press **Command + R** (View → Reload Interface). JARVIS keeps running underneath. |
| JARVIS never reacts to "hey Jarvis" | Speak clearly at normal volume, about an arm's length away. Check System Settings → Sound → Input shows the right microphone. Lower `wake_threshold` to `0.35`. Push-to-talk (Control + Option + J) is a reliable alternative. |
| JARVIS wakes up when nobody called it | Raise `wake_threshold` to `0.7`. |
| It keeps listening long after I've stopped talking | JARVIS ignores sounds much quieter than your voice, but a loud TV or music nearby can still fool it. Move closer to the microphone or turn the noise down. If the timing line says "I kept listening for the full ... s", lower `max_request_seconds` in `config.yaml` (e.g. to `12`). |
| JARVIS "hears" words you never said right after it speaks | It may be hearing the end of its own voice through the speakers. It now waits 0.8 seconds after speaking before it listens. If it still happens, turn the speaker volume down a little or use headphones. |
| It cuts me off before I've finished | Raise `end_of_speech_silence_ms` to `1000`. |
| It ignores what I said / acts like it heard nothing | Very short or unclear phrases are thrown away on purpose, because speech recognition sometimes "hears" words in silence. Speak a full sentence. |
| The push-to-talk shortcut does nothing | Turn on **JARVIS** under **Accessibility** and **Input Monitoring**, then quit and reopen JARVIS. The Space bar in the JARVIS window always works. |
| It keeps asking permission for simple things like web searches | The folder isn't trusted yet. In Terminal run `cd ~/jarvis && claude`, choose "Yes, I trust this folder", type `/exit`, then restart JARVIS. |
| "JARVIS wants access to control ..." pop-up | Click **OK**. That's JARVIS controlling an app you asked about. |
| App control fails, or it says it isn't allowed to control an app | System Settings → Privacy & Security → **Automation** → turn on that app under JARVIS (or Terminal / node). |
| Web browsing fails | Run `./setup.sh` again (it re-downloads the browser), then run `./doctor.sh`. |
| "I can't find Claude Code" | Do setup step 2 again, open a new Terminal window, and run `./setup.sh`. |
| "Claude Code is installed but not signed in" | Type `claude` in Terminal, sign in, type `/exit`, and start JARVIS again. |
| "Sorry, something went wrong" | Ask again. If it keeps happening, run `./doctor.sh`, and look at the end of `logs/jarvis.log`. |
| "Claude took too long to answer" | Usually a slow website or a busy service. Ask again. JARVIS reconnects by itself and remembers the conversation. |
| The timing line says "understood your words" took many seconds | Speech recognition is struggling, usually because the Mac is short of memory. Check the startup line "This Mac has ... GB of memory": if it mentions memory overflowing to the disk, quit other apps (especially web browsers). Keep `whisper_model_apple` on the small model unless your Mac has 16 GB or more. |
| JARVIS takes several seconds to start speaking, or the speech sounds choppy | Run `./doctor.sh --voice`. It times each voice on your Mac, plays three test sentences, and prints the results in plain English. If Kokoro is "too slow", set `tts_engines: [piper, kokoro, say]` in `config.yaml` to use the faster Piper voice. |
| The voice suddenly sounds different or robotic | The main voice (Kokoro) failed, so JARVIS switched to a backup. Run `./setup.sh` again. |
| "There's a problem in config.yaml" | The message names the setting. Fix the spelling or value, or copy that line back from GitHub. |
| Setup stopped while downloading | Check your internet connection and run `./setup.sh` again. It continues where it left off. |
| JARVIS (or a Terminal window) opens every time I log in | That's autostart. Turn it off with `~/jarvis/launchd/install-autostart.sh --remove`. |

## Updating and removing

**Update to the latest version:**
```bash
cd ~/jarvis && git pull && ./setup.sh
```

**Remove JARVIS completely:**
```bash
~/jarvis/launchd/install-autostart.sh --remove
```
Then drag **JARVIS** from your Applications folder to the Bin, and drag the `jarvis` folder to the Bin. Your memory file is inside that folder, so copy
`memory/facts.md` somewhere else first if you want to keep it.

## Logs and privacy

- Audio is **never saved**. The wake word, speech recognition and voices all run on your Mac.
  Only the **text** of what you say goes to Claude.
- Every request, answer, tool use and permission answer is written to
  `logs/conversation-YYYY-MM-DD.jsonl`. Technical details go to `logs/jarvis.log`. These files stay
  on your Mac, and you can delete them whenever you like.
- Web searches go to DuckDuckGo. Websites you ask JARVIS to open see a normal browser visit.

## How it works

1. `openWakeWord` hears "hey Jarvis" (or you press the hotkey).
2. `Silero VAD` notices when you've stopped talking (about 0.7 seconds of silence).
3. `Whisper` turns your speech into English text: `mlx-whisper` on Apple Silicon, `faster-whisper`
   (small, int8) on Intel.
4. The text goes to one long-running `claude -p` process in two-way streaming mode. It runs with all
   `ANTHROPIC_*` settings removed, so it always uses your subscription.
5. Each finished sentence of the reply is spoken straight away by `Kokoro` (backups: `Piper`, then macOS `say`).
6. When Claude needs permission, the request comes back to JARVIS, which asks you out loud and passes on your answer.
7. Tools come from `.mcp.json`, and the permission rules from `.claude/settings.json`.
8. **JARVIS.app** is a small native Mac program (`app/main.swift`). It starts all of the above and
   shows the display. The display is a web page (`jarvis/hud/static/`) served only to this Mac by
   JARVIS. Its live feed and buttons are protected by a random password that changes every launch.
   The 3D core is drawn with [three.js](https://threejs.org) (free and open source, MIT licence),
   which is bundled in `jarvis/hud/static/vendor/three/`, so nothing is downloaded at run time.

## For developers

```bash
uv venv -p 3.12 .venv-test && uv pip install -p .venv-test/bin/python numpy pyyaml pytest
```
```bash
.venv-test/bin/python -m pytest
```

The tests need no microphone, speakers, network or models. A fake `claude`
(`tests/fake_claude.py`) replays real recorded stream-json output from `tests/fixtures/`, including
permission requests. `bash -n` and `shellcheck` should pass on every script. To re-pin
dependencies, run `uv lock && uv export --no-hashes --no-dev -o requirements.txt`.

Package layout: `jarvis/audio.py` (mic, speaker, end-of-speech), `wake.py`, `stt.py`, `tts.py`,
`brain.py` (the `claude` process), `permissions.py` (spoken yes/no), `loop.py` (main loop),
`config.py`, `logs.py`, `doctor.py` (health check), `hud/` (display: event bus, local server, web
page, app icon). The app shell is `app/main.swift`, built by `app/build-app.sh`.

To preview the display without JARVIS running, serve the folder and open the demo:
`cd jarvis/hud/static && python3 -m http.server 8765`, then visit
`http://127.0.0.1:8765/?demo=1`. The 3D modules need a web address; opening the file directly won't
load them. Add `&scene=` with `boot`, `standby`, `muted`, `listening`, `transcribing`, `thinking`,
`speaking`, `confirm` or `settings` to freeze one moment.
To run JARVIS with the display in your web browser instead of the app, use
`.venv/bin/python -m jarvis --app --browser`.
