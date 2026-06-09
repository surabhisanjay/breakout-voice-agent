from __future__ import annotations


class VoiceInput:
    def __init__(
        self,
        model_name: str = "small",
        language: str = "en",
        silence_seconds: float = 0.8,
        speech_threshold: float = 0.012,
        min_speech_seconds: float = 0.25,
        debug: bool = False,
    ):
        self.model_name = model_name
        self.language = language
        self.silence_seconds = silence_seconds
        self.speech_threshold = speech_threshold
        self.min_speech_seconds = min_speech_seconds
        self.debug = debug
        self._whisper = None
        self._model = None
        self.last_status = "empty"

    def available(self) -> bool:
        try:
            import whisper  # noqa: F401
            import sounddevice  # noqa: F401
            import scipy.io.wavfile  # noqa: F401
            return True
        except Exception:
            return False

    def record_and_transcribe(self, seconds: int = 12, sample_rate: int = 16000) -> str:
        try:
            import tempfile

            import numpy as np
            import scipy.io.wavfile
            import sounddevice as sd
            import whisper
        except Exception as exc:
            raise RuntimeError(
                "Voice input requires openai-whisper, sounddevice, scipy, and numpy. "
                "Install requirements and ensure a microphone is available."
            ) from exc

        try:
            if self._model is None:
                self._model = whisper.load_model(self.model_name)
        except Exception:
            self.last_status = "unclear"
            return ""

        if self.debug:
            print("Listening...")
        audio = self._record_until_silence(sd, np, sample_rate, max_seconds=seconds)
        if audio.size == 0:
            self.last_status = "empty"
            return ""

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav_file:
                scipy.io.wavfile.write(wav_file.name, sample_rate, np.squeeze(audio))
                result = self._model.transcribe(
                    wav_file.name,
                    language="en",
                    fp16=False,
                    condition_on_previous_text=False,
                )
        except Exception:
            self.last_status = "unclear"
            return ""

        text = str(result.get("text", "")).strip()
        segments = result.get("segments") or []
        no_speech_prob = float(segments[0].get("no_speech_prob", 0.0)) if segments else 1.0
        avg_logprob = float(segments[0].get("avg_logprob", 0.0)) if segments else -99.0

        if not text:
            self.last_status = "empty"
            return ""

        if no_speech_prob > 0.75:
            self.last_status = "empty"
            return ""

        if avg_logprob < -1.2:
            self.last_status = "unclear"
            return ""

        self.last_status = "ok"
        return text

    def _record_until_silence(self, sd, np, sample_rate: int, max_seconds: int):
        block_size = int(sample_rate * 0.05)
        silence_blocks_needed = max(1, int(self.silence_seconds / 0.05))
        speech_blocks_needed = max(1, int(self.min_speech_seconds / 0.05))
        max_blocks = max(1, int(max_seconds / 0.05))
        pre_roll_blocks = 6

        frames = []
        pre_roll = []
        has_speech = False
        speech_blocks = 0
        silent_blocks = 0

        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
        ) as stream:
            for _ in range(max_blocks):
                block, _ = stream.read(block_size)
                rms = float(np.sqrt(np.mean(np.square(block))))

                if not has_speech:
                    pre_roll.append(block.copy())
                    pre_roll = pre_roll[-pre_roll_blocks:]

                    if rms >= self.speech_threshold:
                        speech_blocks += 1
                    else:
                        speech_blocks = 0

                    if speech_blocks >= speech_blocks_needed:
                        has_speech = True
                        frames.extend(pre_roll)
                        silent_blocks = 0

                    continue

                frames.append(block.copy())

                if rms < self.speech_threshold:
                    silent_blocks += 1

                    if silent_blocks >= silence_blocks_needed:
                        break
                else:
                    silent_blocks = 0

        if not frames:
            return np.array([], dtype="float32")

        return np.concatenate(frames, axis=0)
