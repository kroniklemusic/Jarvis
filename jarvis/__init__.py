"""JARVIS: a local voice assistant for macOS powered by the Claude Code CLI."""

import os

__version__ = "0.1.0"

# Lets PyTorch run Kokoro on the Apple Silicon graphics chip ("mps") and quietly use the
# processor for any step the graphics chip can't do. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
