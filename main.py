import asyncio
import os
import random
import tempfile
import traceback
from pathlib import Path
from typing import Optional

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

from .voice_manager import VoiceManager
from .mimo_client import MiMoClient

PLUGIN_DATA_DIR_NAME = "astrbot_plugin_mimo_tts"


class MiMoTTSPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)

        data_base = Path(os.path.join(
            context.get_data_dir() if hasattr(context, 'get_data_dir') else "data",
            PLUGIN_DATA_DIR_NAME,
        ))
        data_base.mkdir(parents=True, exist_ok=True)

        self._astrbot_config = config if config is not None else AstrBotConfig()
        self.voice_mgr = VoiceManager(data_base)
        self._client: Optional[MiMoClient] = None

    # ── config helpers ──────────────────────────────────────────
    def _cfg(self, key: str, default=None):
        """Read a config value from AstrBotConfig."""
        if self._astrbot_config and key in self._astrbot_config:
            val = self._astrbot_config[key]
            if val is not None and val != "":
                return val
        # Fallback to env for api_key
        if key == "api_key":
            env_val = os.environ.get("MIMO_API_KEY", "")
            if env_val:
                return env_val
        return default

    async def _get_style(self, event: AstrMessageEvent = None) -> str | None:
        """Get the TTS voice style, auto-detected from persona when available.

        Priority:
        1. Manually configured default_style (if set)
        2. Current persona's system prompt (auto-detected)
        3. None (MiMo default)
        """
        # 1. Manual override always wins
        manual = self._cfg("default_style", "")
        if manual:
            return manual

        # 2. Auto-detect from persona
        if event is not None:
            persona_prompt = await self._get_persona_style(event)
            if persona_prompt:
                return persona_prompt

        return None

    async def _get_persona_style(self, event: AstrMessageEvent) -> str | None:
        """Extract voice style hint from the current session's persona."""
        try:
            pm = getattr(self.context, "persona_manager", None)
            if pm is None:
                return None

            # Get umo (unified message origin) from the event
            umo = getattr(event, "umo", None) or getattr(event, "session_id", None)
            if not umo:
                umo_obj = getattr(event, "unified_msg_origin", None)
                if umo_obj:
                    umo = str(umo_obj)

            if not umo:
                return None

            persona = await pm.get_default_persona_v3(umo)
            if persona is None:
                return None

            prompt = persona.get("prompt", "")
            if not prompt:
                return None

            # The system prompt can be very long; MiMo style works best
            # with concise descriptions. Take the first sentence or ~80 chars.
            first_sentence = prompt.split("。")[0].split("\n")[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:120]
            return first_sentence

        except Exception:
            logger.debug(f"Failed to get persona style: {traceback.format_exc()}")
            return None

    def _cfg_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool = False) -> bool:
        val = self._cfg(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)

    @property
    def enabled(self) -> bool:
        return self._cfg_bool("enable", True)

    @property
    def client(self) -> MiMoClient:
        if self._client is None:
            key = self._cfg("api_key", "")
            if not key:
                raise RuntimeError(
                    "MiMo API Key 未配置。请在 WebUI 插件配置中设置，"
                    "或设置环境变量 MIMO_API_KEY"
                )
            self._client = MiMoClient(key)
        return self._client

    def _reload_client(self):
        self._client = None

    # ── auto-TTS: on_message ────────────────────────────────────
    @filter.on_message(filter_type=filter.MessageType.ALL)
    async def on_message_tts(self, event: AstrMessageEvent):
        """根据概率自动将 bot 回复转为语音。"""
        if not self.enabled:
            return

        # Don't intercept commands
        msg_text = event.message_str.strip()
        if msg_text.startswith("/mimo") or msg_text.startswith("/tts"):
            return

        # Check if this is a bot reply (role == assistant)
        role = getattr(event, "role", None)
        if role != "assistant":
            return

        # Probability check
        prob = self._cfg_int("tts_probability", 100)
        if prob <= 0:
            return
        if prob < 100 and random.randint(1, 100) > prob:
            return

        await self._do_tts(event, msg_text)

    async def _do_tts(self, event: AstrMessageEvent, text: str):
        """Core TTS logic shared by auto-TTS and command."""
        if not text or not text.strip():
            return

        text = text.strip()

        # Ban words filter
        ban_words_str = self._cfg("ban_words", "")
        if ban_words_str:
            ban_words = [w.strip() for w in ban_words_str.split("\n") if w.strip()]
            for bw in ban_words:
                if bw in text:
                    logger.info(f"TTS skipped: ban word '{bw}' found in text")
                    return

        # Min chars check
        min_chars = self._cfg_int("min_tts_chars", 2)
        if len(text) < min_chars:
            logger.info(f"TTS skipped: text too short ({len(text)} < {min_chars})")
            return

        # Max chars check
        max_chars = self._cfg_int("max_tts_chars", 200)
        if max_chars > 0 and len(text) > max_chars:
            logger.info(f"TTS skipped: text too long ({len(text)} > {max_chars})")
            return

        voice_name = self.voice_mgr.current_voice
        if not voice_name:
            return  # Silently skip — no voice configured

        try:
            ref_b64 = self.voice_mgr.get_reference_audio_b64(voice_name)
            if not ref_b64:
                return

            fmt = self._cfg("audio_format", "wav")
            style = await self._get_style(event)

            audio_bytes = await self.client.synthesize(
                text=text,
                reference_audio_b64=ref_b64,
                style=style,
                audio_format=fmt,
            )

            yield self._send_audio(event, audio_bytes, fmt)

        except Exception:
            logger.debug(f"Auto-TTS failed: {traceback.format_exc()}")

    # ── /mimo_tts ──────────────────────────────────────────────
    @filter.command("mimo_tts")
    async def cmd_tts(self, event: AstrMessageEvent):
        """将文本转为语音（使用当前克隆音色）"""
        text = event.message_str.strip()
        if text.startswith("/mimo_tts"):
            text = text[len("/mimo_tts"):].strip()

        if not text:
            yield event.plain_result(
                "用法: /mimo_tts <要合成的文本>\n"
                "示例: /mimo_tts 你好世界"
            )
            return

        if not self.enabled:
            yield event.plain_result("TTS 插件未启用，请在 WebUI 配置中开启。")
            return

        voice_name = self.voice_mgr.current_voice
        if not voice_name:
            yield event.plain_result(
                "尚未设置音色。请先用 /mimo_clone 克隆一个音色，"
                "或使用 /mimo_set_voice 选择已克隆的音色。"
            )
            return

        try:
            ref_b64 = self.voice_mgr.get_reference_audio_b64(voice_name)
            if not ref_b64:
                yield event.plain_result(f"音色「{voice_name}」的参考音频丢失，请重新克隆。")
                return

            fmt = self._cfg("audio_format", "wav")
            style = await self._get_style(event)

            yield event.plain_result(f"正在使用音色「{voice_name}」合成语音...")

            audio_bytes = await self.client.synthesize(
                text=text,
                reference_audio_b64=ref_b64,
                style=style,
                audio_format=fmt,
            )

            yield self._send_audio(event, audio_bytes, fmt)

        except RuntimeError as e:
            yield event.plain_result(f"语音合成失败: {e}")
        except Exception:
            logger.error(f"TTS error: {traceback.format_exc()}")
            yield event.plain_result("语音合成时发生未知错误，请查看日志。")

    # ── /mimo_clone ────────────────────────────────────────────
    @filter.command("mimo_clone")
    async def cmd_clone(self, event: AstrMessageEvent):
        """从音频附件克隆新音色。用法: /mimo_clone <音色名>（需同时发送音频文件）"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_clone"):
            raw = raw[len("/mimo_clone"):].strip()

        if not raw:
            yield event.plain_result(
                "用法: /mimo_clone <音色名>\n"
                "请同时发送一段参考音频（语音消息或音频文件）。\n"
                "示例: /mimo_clone 我的声音"
            )
            return

        voice_name = raw

        audio_bytes = await self._extract_audio(event)
        if not audio_bytes:
            yield event.plain_result(
                "未检测到音频附件。请在发送 /mimo_clone 命令时，"
                "同时发送一段语音消息或音频文件（WAV/MP3格式，3-10秒即可）。"
            )
            return

        try:
            yield event.plain_result(f"正在验证音色克隆「{voice_name}」...")

            self.voice_mgr.add_voice(voice_name, audio_bytes)

            ref_b64 = self.voice_mgr.get_reference_audio_b64(voice_name)
            test_audio = await self.client.clone_test(ref_b64)

            self.voice_mgr.set_current_voice(voice_name)

            yield event.plain_result(
                f"音色「{voice_name}」克隆成功！已自动设为当前音色。"
            )

            yield self._send_audio(event, test_audio, self._cfg("audio_format", "wav"))

        except ValueError as e:
            yield event.plain_result(str(e))
        except RuntimeError as e:
            self.voice_mgr.remove_voice(voice_name)
            yield event.plain_result(f"音色克隆失败: {e}")
        except Exception:
            logger.error(f"Clone error: {traceback.format_exc()}")
            self.voice_mgr.remove_voice(voice_name)
            yield event.plain_result("音色克隆时发生未知错误，请查看日志。")

    # ── /mimo_voices ───────────────────────────────────────────
    @filter.command("mimo_voices")
    async def cmd_list_voices(self, event: AstrMessageEvent):
        """列出所有已克隆的音色"""
        voices = self.voice_mgr.list_voices()
        if not voices:
            yield event.plain_result("尚未克隆任何音色。使用 /mimo_clone <名称> 并发送音频来创建。")
            return

        current = self.voice_mgr.current_voice
        lines = ["已克隆的音色:"]
        for v in voices:
            marker = " ★ 当前" if v["name"] == current else ""
            lines.append(f"  - {v['name']}（{v['created_at']}）{marker}")
        yield event.plain_result("\n".join(lines))

    # ── /mimo_set_voice ────────────────────────────────────────
    @filter.command("mimo_set_voice")
    async def cmd_set_voice(self, event: AstrMessageEvent):
        """切换当前使用的音色。用法: /mimo_set_voice <音色名>"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_set_voice"):
            raw = raw[len("/mimo_set_voice"):].strip()

        if not raw:
            voices = self.voice_mgr.list_voices()
            if voices:
                names = ", ".join(v["name"] for v in voices)
                yield event.plain_result(f"可用音色: {names}\n用法: /mimo_set_voice <音色名>")
            else:
                yield event.plain_result("暂无可用音色，请先用 /mimo_clone 创建。")
            return

        success = self.voice_mgr.set_current_voice(raw)
        if success:
            yield event.plain_result(f"当前音色已切换为「{raw}」")
        else:
            yield event.plain_result(f"未找到音色「{raw}」，请使用 /mimo_voices 查看可用音色。")

    # ── /mimo_del_voice ────────────────────────────────────────
    @filter.command("mimo_del_voice")
    async def cmd_del_voice(self, event: AstrMessageEvent):
        """删除已克隆的音色。用法: /mimo_del_voice <音色名>"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_del_voice"):
            raw = raw[len("/mimo_del_voice"):].strip()

        if not raw:
            yield event.plain_result("用法: /mimo_del_voice <音色名>")
            return

        deleted = self.voice_mgr.remove_voice(raw)
        if deleted:
            yield event.plain_result(f"音色「{raw}」已删除。")
        else:
            yield event.plain_result(f"未找到音色「{raw}」。")

    # ── /mimo_style ────────────────────────────────────────────
    @filter.command("mimo_style")
    async def cmd_style(self, event: AstrMessageEvent):
        """设置语音风格（情绪、方言等）。用法: /mimo_style <风格描述>"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_style"):
            raw = raw[len("/mimo_style"):].strip()

        if not raw:
            current = self._cfg("default_style", "") or "(未设置)"
            yield event.plain_result(
                f"当前风格: {current}\n"
                "用法: /mimo_style <风格描述>\n"
                "示例:\n"
                "  /mimo_style 开心\n"
                "  /mimo_style 东北话\n"
                "  /mimo_style 温柔但疲惫\n"
                "  /mimo_style 粤语\n"
                "输入 /mimo_style none 清除风格设置。"
            )
            return

        if raw.lower() == "none":
            self._astrbot_config["default_style"] = ""
            yield event.plain_result("语音风格已清除。")
        else:
            self._astrbot_config["default_style"] = raw
            yield event.plain_result(f"语音风格已设置为「{raw}」")

        self._astrbot_config.save_config()

    # ── /mimo_config ───────────────────────────────────────────
    @filter.command("mimo_config")
    async def cmd_config(self, event: AstrMessageEvent):
        """查看插件配置。完整配置请在 WebUI 插件配置面板中修改。"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_config"):
            raw = raw[len("/mimo_config"):].strip()

        if raw:
            parts = raw.split(maxsplit=1)
            key = parts[0].lower()
            value = parts[1] if len(parts) > 1 else ""

            if key == "apikey":
                if not value:
                    yield event.plain_result("用法: /mimo_config apikey <你的MiMo API Key>")
                    return
                self._astrbot_config["api_key"] = value
                self._astrbot_config.save_config()
                self._reload_client()
                yield event.plain_result("API Key 已更新。")
                return

            elif key == "format":
                if value not in ("wav", "mp3", "pcm16"):
                    yield event.plain_result("格式仅支持: wav, mp3, pcm16")
                    return
                self._astrbot_config["audio_format"] = value
                self._astrbot_config.save_config()
                yield event.plain_result(f"音频格式已设置为 {value}")
                return

        # Display current config
        api_key = self._cfg("api_key", "")
        if api_key:
            masked = api_key[:8] + "****" + api_key[-4:] if len(api_key) > 12 else "****"
        else:
            masked = "(未设置)"

        yield event.plain_result(
            "当前配置:\n"
            f"  API Key: {masked}\n"
            f"  当前音色: {self.voice_mgr.current_voice or '(未设置)'}\n"
            f"  语音风格: {self._cfg('default_style', '') or '(自动跟随人格)'}\n"
            f"  音频格式: {self._cfg('audio_format', 'wav')}\n"
            f"  TTS 概率: {self._cfg_int('tts_probability', 100)}%\n"
            f"  字数限制: {self._cfg_int('min_tts_chars', 2)}-{self._cfg_int('max_tts_chars', 200)}\n"
            f"  启用状态: {'开启' if self.enabled else '关闭'}\n"
            "\n完整配置请在 WebUI 插件配置面板中修改。"
        )

    # ── LLM Tool ────────────────────────────────────────────────
    async def _llm_tool_tts(self, text: str, voice: Optional[str] = None) -> str:
        """LLM function call handler for TTS."""
        if not self._cfg_bool("enable_llm_tool", False):
            return "TTS 工具未启用"

        if self._cfg_bool("llm_tool_char_limit", True):
            ban_words_str = self._cfg("ban_words", "")
            if ban_words_str:
                ban_words = [w.strip() for w in ban_words_str.split("\n") if w.strip()]
                for bw in ban_words:
                    if bw in text:
                        return f"文本包含屏蔽词 '{bw}'，已跳过 TTS"

            min_chars = self._cfg_int("min_tts_chars", 2)
            if len(text) < min_chars:
                return f"文本过短 ({len(text)} < {min_chars})，已跳过 TTS"

            max_chars = self._cfg_int("max_tts_chars", 200)
            if max_chars > 0 and len(text) > max_chars:
                return f"文本过长 ({len(text)} > {max_chars})，已跳过 TTS"

        voice_name = voice or self.voice_mgr.current_voice
        if not voice_name:
            return "未设置音色，请先使用 /mimo_clone 克隆音色"

        try:
            ref_b64 = self.voice_mgr.get_reference_audio_b64(voice_name)
            if not ref_b64:
                return f"音色「{voice_name}」参考音频丢失"

            audio_bytes = await self.client.synthesize(
                text=text,
                reference_audio_b64=ref_b64,
                style=await self._get_style(),
                audio_format=self._cfg("audio_format", "wav"),
            )
            return f"已生成 {len(audio_bytes)} bytes 的语音数据"
        except Exception as e:
            return f"TTS 失败: {e}"

    # ── helpers ─────────────────────────────────────────────────
    async def _extract_audio(self, event: AstrMessageEvent) -> Optional[bytes]:
        """Extract audio bytes from a message attachment if present."""
        attachments = getattr(event, "attachments", None)
        if not attachments:
            attachments = getattr(event, "message", None)
            if hasattr(attachments, "attachments"):
                attachments = attachments.attachments

        if not attachments:
            return None

        for att in attachments:
            url = None
            if isinstance(att, dict):
                url = att.get("url") or att.get("data")
                att_type = att.get("type", "")
                if "audio" not in att_type and "voice" not in att_type:
                    continue
            elif hasattr(att, "url"):
                url = att.url

            if not url:
                continue

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.read()
            except Exception:
                logger.warning(f"Failed to download attachment from {url}")

        return None

    def _send_audio(self, event: AstrMessageEvent, audio_bytes: bytes, fmt: str):
        """Send audio bytes back to the chat."""
        ext = fmt if fmt != "pcm16" else "wav"
        suffix = f".{ext}"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        asyncio.create_task(self._cleanup_file(temp_path))

        voice_method = getattr(event, "voice_result", None)
        if voice_method:
            return voice_method(temp_path)
        else:
            return event.file_result(temp_path)

    async def _cleanup_file(self, path: str, delay: float = 30.0):
        await asyncio.sleep(delay)
        try:
            os.unlink(path)
        except OSError:
            pass

    async def terminate(self):
        """Called when the plugin is unloaded."""
        self._client = None
        logger.info("MiMo TTS plugin terminated.")
