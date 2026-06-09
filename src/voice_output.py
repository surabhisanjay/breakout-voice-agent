from __future__ import annotations

import time


class VoiceOutput:
    """
    pyttsx3-backed text-to-speech output.

    Design guarantees for voice UX (Bugs 3 & 4):
    - runAndWait() blocks until the engine finishes speaking.
    - The microphone is only restarted AFTER speak() returns (enforced in main.py
      via speak_then_resume_listening + POST_TTS_COOLDOWN_SECONDS).
    - We never call stop() mid-speech; stop() is only used for clean shutdown.
    - Long responses are pre-chunked at sentence boundaries so pyttsx3 doesn't
      silently drop text on some backends.

    Debug mode (enabled from main.py when --debug is passed) prints:
        TTS START | CHARS=<n>
        TTS END   | DURATION=<s>s
    """

    # pyttsx3 on macOS/espeak sometimes clips responses longer than this many chars.
    # We split on sentence boundaries to avoid that.
    _CHUNK_THRESHOLD = 300

    def __init__(self, enabled: bool = False, rate: int = 175, debug: bool = False):
        self.enabled = enabled
        self.rate = rate
        self.debug = debug
        self._engine = None

    def speak(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return
        try:
            import pyttsx3
        except Exception as exc:
            raise RuntimeError(
                "Voice output requires pyttsx3. Install requirements.txt first."
            ) from exc

        if self._engine is None:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)

        char_count = len(text)
        if self.debug:
            print(f"TTS START | CHARS={char_count}")

        t0 = time.monotonic()

        chunks = self._split_into_chunks(text)
        for chunk in chunks:
            self._engine.say(chunk)
        self._engine.runAndWait()

        duration = time.monotonic() - t0
        if self.debug:
            print(f"TTS END   | DURATION={duration:.2f}s")

    def stop(self) -> None:
        """Cleanly stop the engine (called only at process exit)."""
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_into_chunks(text: str, threshold: int = _CHUNK_THRESHOLD) -> list[str]:
        """
        Split long text at sentence boundaries so pyttsx3 doesn't clip.
        Returns the original text as a single chunk if it's short enough.
        """
        if len(text) <= threshold:
            return [text]

        # Split on '. ', '? ', '! ' while keeping the punctuation with the sentence.
        import re
        sentences = re.split(r"(?<=[.?!])\s+", text.strip())
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if current and len(current) + 1 + len(sentence) > threshold:
                chunks.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip() if current else sentence
        if current:
            chunks.append(current.strip())
        return chunks if chunks else [text]
