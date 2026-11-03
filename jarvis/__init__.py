"""JARVIS: a local voice assistant for macOS powered by the Claude Code CLI."""

import os
import warnings

__version__ = "0.1.0"

# Lets PyTorch run Kokoro on the Apple Silicon graphics chip ("mps") and quietly use the
# processor for any step the graphics chip can't do. Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# Keep the Terminal window readable: the speech libraries print technical warnings that mean
# nothing to a user and need no action.
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, module=r"(torch|kokoro|huggingface_hub|misaki|spacy)")
