# JARVIS

A voice assistant for your Mac. Say **"hey Jarvis"**, ask for something out loud, and JARVIS
answers out loud using Claude Code on your Mac.

Everything runs on your Mac except Claude itself, which uses your existing Claude Pro or Max
subscription through the official `claude` program. No API key is needed, and JARVIS never uses one.
All the other parts (wake word, speech recognition and voices) are free and open source, and they
run offline once downloaded.

> **Status: Phase 1.** Voice in, Claude, voice out. Tools (controlling apps, browsing the web)
> come in Phase 2. Memory and starting automatically at login come in Phase 3.

---

## What you need

- A Mac (Apple Silicon or Intel) with macOS 13 or newer, a microphone and speakers.
- A Claude Pro or Max subscription.
- About 6 GB of free disk space and an internet connection for the first setup.

## One-time setup

1. **Install Homebrew** if you don't have it. Open the **Terminal** app, go to <https://brew.sh>,
   copy the install line shown there, paste it into Terminal and press Return. Follow what it says.
2. **Install Claude Code and sign in:**
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
   Open a **new** Terminal window, type `claude`, press Return, and sign in with your Claude
   account. When it's ready, type `/exit`.
3. **Download JARVIS and run setup:**
   ```bash
   git clone https://github.com/YOUR-GITHUB-NAME/jarvis.git
   ```
   ```bash
   cd jarvis
   ```
   ```bash
   ./setup.sh
   ```
   Setup installs the helper tools, creates a private Python environment inside the `jarvis` folder,
   and downloads the speech and voice models. The first run takes 10 to 30 minutes, depending on your
   internet speed. You can run `./setup.sh` again at any time, and it skips what's already done.

## Daily use

**Start JARVIS.** In Terminal, inside the `jarvis` folder:

```bash
.venv/bin/python -m jarvis
```

Wait for **"JARVIS is ready"** and a spoken "At your service." Then:

- Say **"hey Jarvis"**. You'll hear a soft *tink*. Then ask your question.
- Stop talking, and after a short pause JARVIS answers out loud.
- For about **8 seconds** after it answers, you can ask a follow-up question **without** saying
  "hey Jarvis" again.
- To **interrupt** JARVIS while it's talking, say "hey Jarvis" again. It stops and listens.
- **Push-to-talk:** instead of saying "hey Jarvis", you can press **Control + Option + J**.

**Stop JARVIS:** click the Terminal window and press **Control + C**.

## Granting Mac permissions

macOS asks for permission the first time JARVIS needs something. If you clicked "Don't Allow" by
mistake, you can turn it back on yourself:

| Permission | Why JARVIS needs it | Where to turn it on |
|---|---|---|
| **Microphone** | To hear you | System Settings → Privacy & Security → **Microphone** → turn on **Terminal** |
| **Accessibility** | For the push-to-talk shortcut | System Settings → Privacy & Security → **Accessibility** → turn on **Terminal** |
| **Input Monitoring** | For the push-to-talk shortcut on some macOS versions | System Settings → Privacy & Security → **Input Monitoring** → turn on **Terminal** |
| **Automation** (Phase 2) | To control apps such as Mail, Calendar or Safari | System Settings → Privacy & Security → **Automation** → under **Terminal**, turn on each app |

After changing a permission, quit JARVIS (Control + C) and start it again.
If you use iTerm or another terminal app instead of Terminal, grant the permissions to that app instead.

## Changing settings

All settings are in **`config.yaml`** in the `jarvis` folder. Open it with TextEdit, change a value,
save, and restart JARVIS. Each setting has a note explaining it.

### Changing the voice

- **Different Kokoro voice** (the default engine): change `kokoro_voice`. British male voices:
  `bm_george` (default), `bm_lewis`, `bm_daniel`, `bm_fable`. British female voices: `bf_emma`,
  `bf_isabella`, `bf_alice`, `bf_lily`. American voices also work, for example `am_michael` or `af_heart`.
- **Speak faster or slower:** change `speech_speed`. For example, `1.15` is a little faster.
- **Use the backup voice engines:** `tts_engines` sets the order JARVIS tries them in. If one
  fails, JARVIS moves to the next one automatically. `say` is the Mac's built-in voice, and
  `say_voice` chooses which one it uses.

### Other useful settings

- `model`: which Claude model answers. The choices are `sonnet` (default), `opus` and `haiku`.
- `wake_threshold`: raise it (e.g. `0.7`) if JARVIS wakes up by accident. Lower it (e.g. `0.35`) if it ignores you.
- `follow_up_seconds`: how long JARVIS listens for a follow-up question. `0` turns follow-ups off.
- `hotkey`: the push-to-talk shortcut.

## Troubleshooting

| What you see or hear | What to do |
|---|---|
| "I can't use the microphone" | Turn on Terminal under System Settings → Privacy & Security → Microphone, then restart JARVIS. |
| JARVIS never reacts to "hey Jarvis" | Speak clearly at normal volume, about an arm's length from the Mac. Lower `wake_threshold` to `0.35`. Check that the right microphone is selected under System Settings → Sound → Input. |
| JARVIS wakes up when nobody called it | Raise `wake_threshold` to `0.7`. |
| It cuts me off before I finish | Raise `end_of_speech_silence_ms` to `1000` in `config.yaml`. |
| The push-to-talk shortcut does nothing | Turn on Terminal under Accessibility and Input Monitoring (see the permissions table above), then restart JARVIS. |
| "I can't find Claude Code" | Install Claude Code (setup step 2), open a new Terminal window and run `./setup.sh` again. |
| "Claude Code is installed but not signed in" | Type `claude` in Terminal, sign in, type `/exit`, and try again. |
| JARVIS says "Sorry, something went wrong" | Ask again. If it keeps happening, look at the end of `logs/jarvis.log`, and check that `claude` works on its own in Terminal. |
| The voice sounds robotic or different | Kokoro failed to start, so JARVIS fell back to a backup voice. Run `./setup.sh` again. |
| There's a problem in config.yaml | The message names the setting. Fix the spelling or value, or copy the line back from the original file. |
| Setup stopped while downloading | Check your internet connection and run `./setup.sh` again. It continues where it left off. |

## Logs and privacy

- Every conversation turn and tool call is written to `logs/conversation-YYYY-MM-DD.jsonl`.
  Technical details go to `logs/jarvis.log`. These files stay on your Mac. Delete them whenever you like.
- Audio is never saved. Speech recognition, the wake word and the voices all run on your Mac.
  Only the **text** of your request goes to Claude.

## How it works (for the curious)

`openWakeWord` hears "hey Jarvis". `Silero VAD` notices when you stop talking. `Whisper` turns
your speech into text (mlx-whisper on Apple Silicon, faster-whisper on Intel). The text goes to one
long-running `claude -p` process in streaming mode. Each sentence of the reply is spoken by
`Kokoro` (or Piper, or the Mac's `say` command) as soon as it's complete.

## For developers

```bash
uv venv -p 3.12 .venv-test && uv pip install -p .venv-test/bin/python numpy pyyaml pytest
```
```bash
.venv-test/bin/python -m pytest
```

The tests need no microphone, speakers, network or models. A fake `claude`
(`tests/fake_claude.py`) replays recorded stream-json output from `tests/fixtures/`.
To re-pin dependencies, run `uv lock && uv export --no-hashes --no-dev -o requirements.txt`.
