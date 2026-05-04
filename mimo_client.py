import base64
import logging
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_TTS_MODEL = "mimo-v2-5-tts-voiceclone"


class MiMoClient:
    def __init__(self, api_key: str, base_url: str = MIMO_BASE_URL):
        if not api_key:
            raise ValueError("MiMo API key is required")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def synthesize(
        self,
        text: str,
        *,
        reference_audio_b64: Optional[str] = None,
        voice: Optional[str] = None,
        style: Optional[str] = None,
        audio_format: str = "wav",
    ) -> bytes:
        if not text.strip():
            raise ValueError("Text to synthesize cannot be empty")

        audio_params: dict = {"format": audio_format}
        if reference_audio_b64:
            audio_params["reference_audio"] = reference_audio_b64
        if voice:
            audio_params["voice"] = voice

        # Apply style as XML tag in the text content
        content = text.strip()
        if style:
            content = f"<style>{style}</style>{content}"

        try:
            response = await self._client.chat.completions.create(
                model=MIMO_TTS_MODEL,
                messages=[{"role": "assistant", "content": content}],
                audio=audio_params,
            )
        except Exception as e:
            logger.error(f"MiMo API call failed: {e}")
            raise RuntimeError(f"MiMo语音合成失败: {e}") from e

        choice = response.choices[0]
        audio_data = choice.message.audio
        if audio_data is None or not audio_data.data:
            raise RuntimeError("MiMo API returned no audio data")

        return base64.b64decode(audio_data.data)

    async def clone_test(
        self,
        reference_audio_b64: str,
        audio_format: str = "wav",
    ) -> bytes:
        """Test voice cloning by synthesizing a short test phrase."""
        return await self.synthesize(
            text="你好，这是来自小米MiMo语音克隆的测试。",
            reference_audio_b64=reference_audio_b64,
            audio_format=audio_format,
        )
