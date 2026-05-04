import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

VOICES_FILENAME = "mimo_tts_voices.json"


class VoiceManager:
    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._voices_dir = data_dir / "voices"
        self._index_path = data_dir / VOICES_FILENAME
        self._voices: dict[str, dict] = {}
        self._current_voice: Optional[str] = None
        self._load()

    def _load(self):
        if self._index_path.exists():
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    self._voices = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._voices = {}

    def _save(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._voices, f, ensure_ascii=False, indent=2)

    def add_voice(self, name: str, audio_bytes: bytes) -> str:
        """Register a new cloned voice from reference audio bytes.

        Returns the voice name (normalized).
        """
        name = name.strip()
        if not name:
            raise ValueError("Voice name cannot be empty")

        # Hash audio to avoid duplicates
        audio_hash = hashlib.sha256(audio_bytes).hexdigest()[:16]
        ext = ".wav"

        # Check if same audio already registered under a different name
        for existing_name, v in self._voices.items():
            if v.get("audio_hash") == audio_hash:
                raise ValueError(
                    f"相同的音频已注册为音色「{existing_name}」，"
                    f"音频指纹: {audio_hash}"
                )

        # Save reference audio
        self._voices_dir.mkdir(parents=True, exist_ok=True)
        audio_filename = f"{name}_{audio_hash}{ext}"
        audio_path = self._voices_dir / audio_filename
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        self._voices[name] = {
            "name": name,
            "audio_hash": audio_hash,
            "audio_path": str(audio_path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._save()
        return name

    def remove_voice(self, name: str) -> bool:
        """Delete a cloned voice. Returns True if deleted."""
        name = name.strip()
        if name not in self._voices:
            return False

        v = self._voices[name]
        audio_path = Path(v["audio_path"])
        if audio_path.exists():
            audio_path.unlink()

        del self._voices[name]
        if self._current_voice == name:
            self._current_voice = None
        self._save()
        return True

    def get_voice(self, name: str) -> Optional[dict]:
        """Get voice metadata by name."""
        return self._voices.get(name.strip())

    def get_reference_audio_b64(self, name: str) -> Optional[str]:
        """Get base64-encoded reference audio for a voice."""
        v = self._voices.get(name.strip())
        if not v:
            return None
        audio_path = Path(v["audio_path"])
        if not audio_path.exists():
            return None
        with open(audio_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def list_voices(self) -> list[dict]:
        """List all registered voices."""
        return list(self._voices.values())

    @property
    def current_voice(self) -> Optional[str]:
        return self._current_voice

    @current_voice.setter
    def current_voice(self, name: Optional[str]):
        if name is not None and name.strip() not in self._voices:
            raise ValueError(f"Voice '{name}' not found")
        self._current_voice = name.strip() if name else None

    def set_current_voice(self, name: str) -> bool:
        """Set the active voice. Returns True on success."""
        name = name.strip()
        if name not in self._voices:
            return False
        self._current_voice = name
        return True
