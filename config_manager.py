import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = {
    "api_key": "",
    "default_voice": None,
    "default_style": "",
    "audio_format": "wav",
}

CONFIG_FILENAME = "mimo_tts_config.json"


class ConfigManager:
    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._config_path = data_dir / CONFIG_FILENAME
        self._config: dict = {}
        self._load()

    def _load(self):
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._config = {}
        # Merge with defaults for missing keys
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._config:
                self._config[key] = value

    def _save(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    @property
    def api_key(self) -> str:
        return os.environ.get("MIMO_API_KEY", self._config.get("api_key", ""))

    @api_key.setter
    def api_key(self, value: str):
        self._config["api_key"] = value
        self._save()

    @property
    def default_voice(self) -> Optional[str]:
        return self._config.get("default_voice")

    @default_voice.setter
    def default_voice(self, value: Optional[str]):
        self._config["default_voice"] = value
        self._save()

    @property
    def default_style(self) -> str:
        return self._config.get("default_style", "")

    @default_style.setter
    def default_style(self, value: str):
        self._config["default_style"] = value
        self._save()

    @property
    def audio_format(self) -> str:
        return self._config.get("audio_format", "wav")

    @audio_format.setter
    def audio_format(self, value: str):
        if value not in ("wav", "mp3", "pcm16"):
            raise ValueError(f"Unsupported audio format: {value}")
        self._config["audio_format"] = value
        self._save()

    def get_all(self) -> dict:
        """Return a copy of all config for display (mask api_key)."""
        config = dict(self._config)
        if config.get("api_key"):
            config["api_key"] = config["api_key"][:8] + "****" + config["api_key"][-4:]
        else:
            env_key = os.environ.get("MIMO_API_KEY", "")
            if env_key:
                config["api_key"] = "(from env) " + env_key[:8] + "****" + env_key[-4:]
            else:
                config["api_key"] = "(not set)"
        return config
