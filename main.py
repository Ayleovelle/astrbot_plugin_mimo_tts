import asyncio
import os
import tempfile
import traceback
from pathlib import Path

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

from .config_manager import ConfigManager
from .voice_manager import VoiceManager
from .mimo_client import MiMoClient

PLUGIN_DATA_DIR_NAME = "astrbot_plugin_mimo_tts"


class MiMoTTSPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        # Set up data directory
        data_base = Path(os.path.join(
            context.get_data_dir() if hasattr(context, 'get_data_dir') else "data",
            PLUGIN_DATA_DIR_NAME,
        ))
        data_base.mkdir(parents=True, exist_ok=True)

        self.config = ConfigManager(data_base)
        self.voice_mgr = VoiceManager(data_base)
        self._client: MiMoClient | None = None

    @property
    def client(self) -> MiMoClient:
        if self._client is None:
            key = self.config.api_key
            if not key:
                raise RuntimeError(
                    "MiMo API Key 未配置。请使用 /mimo_config 设置，"
                    "或设置环境变量 MIMO_API_KEY"
                )
            self._client = MiMoClient(key)
        return self._client

    def _reload_client(self):
        self._client = None

    # ── /mimo_tts ──────────────────────────────────────────────
    @filter.command("mimo_tts")
    async def cmd_tts(self, event: AstrMessageEvent):
        """将文本转为语音（使用当前克隆音色）"""
        text = event.message_str.strip()
        # Remove the command prefix
        if text.startswith("/mimo_tts"):
            text = text[len("/mimo_tts"):].strip()

        if not text:
            yield event.plain_result(
                "用法: /mimo_tts <要合成的文本>\n"
                "示例: /mimo_tts 你好世界"
            )
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

            style = self.config.default_style or None
            fmt = self.config.audio_format

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

        # Extract audio attachment from the message
        audio_bytes = await self._extract_audio(event)
        if not audio_bytes:
            yield event.plain_result(
                "未检测到音频附件。请在发送 /mimo_clone 命令时，"
                "同时发送一段语音消息或音频文件（WAV/MP3格式，3-10秒即可）。"
            )
            return

        try:
            yield event.plain_result(f"正在验证音色克隆「{voice_name}」...")

            # Register the voice (saves reference audio)
            self.voice_mgr.add_voice(voice_name, audio_bytes)

            # Test the clone
            ref_b64 = self.voice_mgr.get_reference_audio_b64(voice_name)
            test_audio = await self.client.clone_test(ref_b64)

            # Set as current voice
            self.voice_mgr.set_current_voice(voice_name)

            yield event.plain_result(
                f"音色「{voice_name}」克隆成功！已自动设为当前音色。"
            )

            # Send test audio
            yield self._send_audio(event, test_audio, self.config.audio_format)

        except ValueError as e:
            yield event.plain_result(str(e))
        except RuntimeError as e:
            # Clean up failed clone
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
            current = self.config.default_style or "(未设置)"
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
            self.config.default_style = ""
            yield event.plain_result("语音风格已清除。")
        else:
            self.config.default_style = raw
            yield event.plain_result(f"语音风格已设置为「{raw}」")

    # ── /mimo_config ───────────────────────────────────────────
    @filter.command("mimo_config")
    async def cmd_config(self, event: AstrMessageEvent):
        """查看或修改插件配置。用法: /mimo_config [key] [value]"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_config"):
            raw = raw[len("/mimo_config"):].strip()

        if not raw:
            cfg = self.config.get_all()
            fmt = self.config.audio_format
            style = self.config.default_style or "(未设置)"
            voice = self.voice_mgr.current_voice or "(未设置)"

            yield event.plain_result(
                "当前配置:\n"
                f"  API Key: {cfg['api_key']}\n"
                f"  当前音色: {voice}\n"
                f"  语音风格: {style}\n"
                f"  音频格式: {fmt}\n"
                "\n修改配置:\n"
                "  /mimo_config apikey <key>\n"
                "  /mimo_config format <wav|mp3|pcm16>"
            )
            return

        parts = raw.split(maxsplit=1)
        key = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""

        if key == "apikey":
            if not value:
                yield event.plain_result("用法: /mimo_config apikey <你的MiMo API Key>")
                return
            self.config.api_key = value
            self._reload_client()
            yield event.plain_result("API Key 已更新。")

        elif key == "format":
            if value not in ("wav", "mp3", "pcm16"):
                yield event.plain_result("格式仅支持: wav, mp3, pcm16")
                return
            self.config.audio_format = value
            yield event.plain_result(f"音频格式已设置为 {value}")

        else:
            yield event.plain_result(f"未知配置项: {key}。支持: apikey, format")

    # ── helpers ─────────────────────────────────────────────────
    async def _extract_audio(self, event: AstrMessageEvent) -> bytes | None:
        """Extract audio bytes from a message attachment if present."""
        # Try to get attachments from the event
        attachments = getattr(event, "attachments", None)
        if not attachments:
            # Some platforms store attachments differently
            attachments = getattr(event, "message", None)
            if hasattr(attachments, "attachments"):
                attachments = attachments.attachments

        if not attachments:
            return None

        for att in attachments:
            url = None
            if isinstance(att, dict):
                url = att.get("url") or att.get("data")
                # Check if it's audio type
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
        """Send audio bytes back to the chat. Returns a result to be yielded."""
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
