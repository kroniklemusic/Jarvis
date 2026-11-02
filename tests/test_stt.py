import pytest

from jarvis.stt import clean_transcript, detect_chip


def test_detect_chip():
    assert detect_chip("Darwin", "arm64") == "apple"
    assert detect_chip("Darwin", "x86_64") == "intel"
    assert detect_chip("Linux", "x86_64") == "intel"


@pytest.mark.parametrize("text", [
    None, "", "   ", ".", "...", " Thank you for watching!", "Thanks for watching.",
    "[Music]", "(applause)", "Thank you. Thank you. Thank you.", "you", " Please subscribe!",
    "Subtitles by the Amara.org community",
])
def test_drops_empty_and_hallucinated_transcripts(text):
    assert clean_transcript(text) is None


@pytest.mark.parametrize("text", [
    "What's the weather like tomorrow?",
    "Thank you for watching the kids last night, can you remind me to call Sam?",
    "yes, proceed",
    "no",
])
def test_keeps_real_requests(text):
    assert clean_transcript("  " + text + " ") == text
