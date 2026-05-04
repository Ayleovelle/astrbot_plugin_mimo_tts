import asyncio
import io
import os
import random
import string
import tempfile
import time
import traceback
import wave
from pathlib import Path
from typing import Optional

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.config import AstrBotConfig

try:
    from astrbot.api.message_components import Record
except ImportError:
    Record = None

from .voice_manager import VoiceManager
from .mimo_client import MiMoClient

PLUGIN_DATA_DIR_NAME = "astrbot_plugin_mimo_tts"


@register(
    "astrbot_plugin_mimo_tts",
    "Ayleovelle",
    "基于小米MiMo-V2.5-TTS-VoiceClone引擎的语音克隆与文本转语音插件",
    "0.1.3-beta",
)
class MiMoTTSPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        data_base = Path(os.path.join(
            context.get_data_dir() if hasattr(context, 'get_data_dir') else "data",
            PLUGIN_DATA_DIR_NAME,
        ))
        data_base.mkdir(parents=True, exist_ok=True)

        self._astrbot_config = config
        self.voice_mgr = VoiceManager(data_base)
        self._client: Optional[MiMoClient] = None
        self._pending_clones: dict[str, dict] = {}

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

    @staticmethod
    def _user_key(event: AstrMessageEvent) -> str:
        return f"{event.session_id}_{event.get_sender_id()}"

    @staticmethod
    def _generate_password(length: int = 10) -> str:
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k=length))

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """Check if sender is admin (system admin or configured admin_users)."""
        if event.is_admin():
            return True
        sender = event.get_sender_id()
        admin_users = self._cfg("admin_users", "")
        if admin_users:
            admins = [u.strip() for u in admin_users.split("\n") if u.strip()]
            if sender in admins:
                return True
        return False

    # ── auto-TTS: on_message ────────────────────────────────────
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message_tts(self, event: AstrMessageEvent):
        """根据概率自动将 bot 回复转为语音，同时处理克隆流程中的待确认消息。"""
        if not self.enabled:
            return

        # Don't intercept commands (except /mimo_clone_end during pending flow)
        msg_text = event.message_str.strip()
        is_clone_end = msg_text.startswith("/mimo_clone_end")
        if (msg_text.startswith("/mimo") or msg_text.startswith("/tts")) and not is_clone_end:
            return

        # ── pending clone flow handling ──
        uk = self._user_key(event)
        pending = self._pending_clones.get(uk)
        if pending:
            event.stop_event()  # 阻断整个管道，防止 LLM 回复
            if is_clone_end:
                del self._pending_clones[uk]
                yield event.plain_result("已终止克隆流程。")
                return
            if time.time() > pending.get("expire", 0):
                del self._pending_clones[uk]
                yield event.plain_result("操作超时，已取消。")
                return
            state = pending["state"]
            if state == "waiting_audio":
                yield self._handle_waiting_audio(event, uk, pending)
                return
            elif state == "confirming":
                yield self._handle_confirming(event, uk, pending, msg_text)
                return
            elif state == "deleting":
                yield self._handle_deleting(event, uk, pending, msg_text)
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

        result = await self._do_tts(event, msg_text)
        if result:
            yield result

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

            return self._send_audio(event, audio_bytes, fmt)

        except Exception:
            logger.debug(f"Auto-TTS failed: {traceback.format_exc()}")
            return None

    # ── pending clone state handlers ───────────────────────────
    async def _handle_waiting_audio(self, event: AstrMessageEvent, uk: str, pending: dict):
        """Handle message when waiting for audio in clone flow."""
        # Step 1: 下载文件到服务端
        yield event.plain_result("下载中...")
        audio_bytes = await self._extract_audio(event)
        if not audio_bytes:
            return  # Not an audio message, ignore

        # Step 2: 保存到临时文件
        temp_path = None
        try:
            temp_path = self._save_temp_audio(audio_bytes, ".tmp")
        except PermissionError:
            yield event.plain_result(
                "文件写入失败：权限不足，无法将音频保存到服务端。\n\n"
                "这通常是因为 AstrBot 运行在受限环境（如 Docker 容器或沙箱）中。\n"
                "请检查以下事项：\n"
                "  1. 确认插件 data 目录有写入权限\n"
                "  2. 若使用 Docker，确保挂载了可写卷\n"
                "  3. 如有必要，可尝试以管理员权限运行 AstrBot\n\n"
                "警告：以管理员/root 权限运行存在安全风险，请谨慎操作。"
            )
            return
        except OSError as e:
            logger.error(f"Failed to save temp audio: {e}")
            yield event.plain_result(f"文件保存失败: {e}")
            return

        # Step 3: 识别文件格式
        info = self._analyze_audio(audio_bytes)
        size_kb = info["size"] / 1024

        # 非音频文件
        if info["format"] == "未知":
            self._cleanup_temp_file(temp_path)
            yield event.plain_result(
                f"下载完成 ({size_kb:.1f} KB)，但该文件不是音频文件。\n"
                "请发送语音消息或音频文件（支持 WAV、MP3、OGG、FLAC、M4A 等格式）。"
            )
            return

        duration_str = f"{info['duration']:.1f}秒" if info["duration"] else "未知"
        yield event.plain_result(
            f"下载完成 ({size_kb:.1f} KB)\n"
            f"识别成功\n"
            f"  格式: {info['format']}\n"
            f"  时长: {duration_str}\n"
            f"  采样率: {info['sample_rate']} Hz\n"
            f"  声道数: {info['channels']}"
        )

        # Step 4: 格式检查与转换
        if not self._is_audio_format_supported(info["format"]):
            ffmpeg = self._find_ffmpeg()
            if not ffmpeg:
                self._cleanup_temp_file(temp_path)
                yield event.plain_result(
                    f"格式 {info['format']} 不受 MiMo 模型支持，且未检测到 ffmpeg。\n"
                    "请安装 ffmpeg 后重试，或直接发送 WAV/MP3 格式的音频。"
                )
                return

            yield event.plain_result(f"格式转换中（{info['format']} → WAV）...")
            try:
                converted_path = await self._convert_to_wav(temp_path)
                self._cleanup_temp_file(temp_path)
                temp_path = converted_path
                # Re-analyze converted file
                with open(converted_path, "rb") as f:
                    audio_bytes = f.read()
                info = self._analyze_audio(audio_bytes)
                yield event.plain_result(
                    f"转换完成\n"
                    f"  格式: {info['format']}\n"
                    f"  大小: {info['size']/1024:.1f} KB\n"
                    f"  采样率: {info['sample_rate']} Hz"
                )
            except FileNotFoundError:
                self._cleanup_temp_file(temp_path)
                yield event.plain_result(
                    f"格式 {info['format']} 不受支持，且 ffmpeg 执行失败。\n"
                    "请安装 ffmpeg 后重试，或直接发送 WAV/MP3 格式的音频。"
                )
                return
            except RuntimeError as e:
                self._cleanup_temp_file(temp_path)
                yield event.plain_result(f"音频格式转换失败: {e}")
                return

        # Update pending state
        pending["state"] = "confirming"
        pending["temp_path"] = temp_path
        pending["audio_info"] = info
        pending["expire"] = time.time() + 60
        name = pending["name"]

        yield event.plain_result(
            f"\n是否使用此音频克隆音色「{name}」？\n"
            f"回复 确认 进行克隆，回复 取消 或 /mimo_clone_end 放弃。"
        )

    async def _handle_confirming(self, event: AstrMessageEvent, uk: str, pending: dict, msg_text: str):
        """Handle confirmation reply in clone flow."""
        if msg_text == "确认":
            name = pending["name"]
            temp_path = pending.get("temp_path")
            del self._pending_clones[uk]

            # Step 3: 克隆中
            yield event.plain_result("克隆中...")
            try:
                # Read audio from temp file
                if temp_path and os.path.exists(temp_path):
                    with open(temp_path, "rb") as f:
                        audio_bytes = f.read()
                else:
                    yield event.plain_result("临时音频文件丢失，请重新发送音频。")
                    return

                self.voice_mgr.add_voice(name, audio_bytes)
                ref_b64 = self.voice_mgr.get_reference_audio_b64(name)

                # Step 4: 生成测试音频
                test_audio = await self.client.clone_test(ref_b64)
                self.voice_mgr.set_current_voice(name)

                # Step 5: 完成
                yield event.plain_result(f"已完成！音色「{name}」克隆成功，已自动设为当前音色。")
                yield self._send_audio(event, test_audio, self._cfg("audio_format", "wav"))

            except PermissionError:
                yield event.plain_result(
                    "文件操作失败：权限不足。\n\n"
                    "警告：以管理员/root 权限运行存在安全风险，请谨慎操作。\n"
                    "建议通过 Docker 挂载可写卷或修改目录权限来解决。"
                )
                self.voice_mgr.remove_voice(name)
            except ValueError as e:
                yield event.plain_result(str(e))
            except RuntimeError as e:
                self.voice_mgr.remove_voice(name)
                yield event.plain_result(f"音色克隆失败: {e}")
            except Exception:
                logger.error(f"Clone error: {traceback.format_exc()}")
                self.voice_mgr.remove_voice(name)
                yield event.plain_result("音色克隆时发生未知错误，请查看日志。")
            finally:
                # 清理临时文件
                if temp_path:
                    self._cleanup_temp_file(temp_path)

        elif msg_text == "取消":
            temp_path = pending.get("temp_path")
            del self._pending_clones[uk]
            if temp_path:
                self._cleanup_temp_file(temp_path)
            yield event.plain_result("已取消音色克隆。")
        # else: ignore unrelated messages

    async def _handle_deleting(self, event: AstrMessageEvent, uk: str, pending: dict, msg_text: str):
        """Handle password confirmation in voice delete flow."""
        expected = pending.get("password", "")
        name = pending.get("name", "")
        if msg_text.strip() == expected:
            deleted = self.voice_mgr.remove_voice(name)
            del self._pending_clones[uk]
            if deleted:
                yield event.plain_result(f"音色「{name}」已删除。")
            else:
                yield event.plain_result(f"音色「{name}」不存在或已被删除。")
        else:
            del self._pending_clones[uk]
            yield event.plain_result("密码错误，已取消删除操作。")

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
        """开始克隆流程。用法: /mimo_clone <音色名>（随后发送音频文件）"""
        raw = event.message_str.strip()
        if raw.startswith("/mimo_clone"):
            raw = raw[len("/mimo_clone"):].strip()

        if not raw:
            yield event.plain_result(
                "用法: /mimo_clone <音色名>\n"
                "发送命令后，在限定时间内发送一段语音消息或音频文件即可。\n"
                "示例: /mimo_clone 我的声音"
            )
            return

        timeout = self._cfg_int("clone_timeout", 60)
        uk = self._user_key(event)
        self._pending_clones[uk] = {
            "state": "waiting_audio",
            "name": raw,
            "expire": time.time() + timeout,
        }
        yield event.plain_result(
            f"请在 {timeout} 秒内发送一段语音消息或音频文件（3-10秒），"
            f"用于克隆音色「{raw}」。\n"
            f"发送 /mimo_clone_end 可随时终止流程。"
        )

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

    # ── /mimo_my_voice (admin only) ────────────────────────────
    @filter.command("mimo_my_voice")
    async def cmd_my_voice(self, event: AstrMessageEvent):
        """列出所有已克隆的音色（仅管理员）"""
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可使用此命令。")
            return
        voices = self.voice_mgr.list_voices()
        if not voices:
            yield event.plain_result("尚未克隆任何音色。使用 /mimo_clone 创建。")
            return

        current = self.voice_mgr.current_voice
        lines = ["已克隆的音色:"]
        for v in voices:
            marker = " ★ 当前" if v["name"] == current else ""
            lines.append(f"  - {v['name']}（{v['created_at']}）{marker}")
        yield event.plain_result("\n".join(lines))

    # ── /mimo_voice_delete (admin only + password confirm) ────
    @filter.command("mimo_voice_delete")
    async def cmd_voice_delete(self, event: AstrMessageEvent):
        """删除音色（需密码确认）。用法: /mimo_voice_delete <音色名>"""
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可使用此命令。")
            return
        raw = event.message_str.strip()
        if raw.startswith("/mimo_voice_delete"):
            raw = raw[len("/mimo_voice_delete"):].strip()

        if not raw:
            yield event.plain_result("用法: /mimo_voice_delete <音色名>")
            return

        if not self.voice_mgr.get_voice(raw):
            yield event.plain_result(f"未找到音色「{raw}」。")
            return

        pwd = self._generate_password()
        timeout = self._cfg_int("clone_timeout", 60)
        uk = self._user_key(event)
        self._pending_clones[uk] = {
            "state": "deleting",
            "name": raw,
            "password": pwd,
            "expire": time.time() + timeout,
        }
        yield event.plain_result(
            f"确认删除音色「{raw}」？请在 {timeout} 秒内输入以下密码:\n{pwd}"
        )

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

    # ── /mimo_clone_end ───────────────────────────────────────
    @filter.command("mimo_clone_end")
    async def cmd_clone_end(self, event: AstrMessageEvent):
        """终止当前克隆流程"""
        uk = self._user_key(event)
        if uk in self._pending_clones:
            del self._pending_clones[uk]
            yield event.plain_result("已终止克隆流程。")
        else:
            yield event.plain_result("当前没有正在进行的克隆流程。")

    # ── ffmpeg ─────────────────────────────────────────────────
    _ffmpeg_path: Optional[str] = None  # cached path

    @classmethod
    def _find_ffmpeg(cls) -> Optional[str]:
        """Auto-detect ffmpeg binary path. Searches in order:
        1. Cached result
        2. System PATH (shutil.which)
        3. Common install directories
        """
        if cls._ffmpeg_path is not None:
            return cls._ffmpeg_path

        import shutil
        # 1. System PATH
        found = shutil.which("ffmpeg")
        if found:
            cls._ffmpeg_path = found
            return found

        # 2. Common paths
        candidates = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "C:/ffmpeg/bin/ffmpeg.exe",
            "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                cls._ffmpeg_path = p
                return p

        return None

    @staticmethod
    def _is_audio_format_supported(fmt: str) -> bool:
        """Check if format is directly supported by MiMo-V2.5-TTS-VoiceClone."""
        return fmt.lower() in ("wav", "mp3")

    async def _convert_to_wav(self, input_path: str) -> str:
        """Convert audio file to WAV (16kHz mono) using ffmpeg.
        Returns path to converted WAV file. Raises FileNotFoundError if ffmpeg not found.
        """
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg")

        output_path = input_path.rsplit(".", 1)[0] + "_converted.wav"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode()[:200]}")
        return output_path

    # ── helpers ─────────────────────────────────────────────────
    @staticmethod
    def _analyze_audio(audio_bytes: bytes) -> dict:
        """Analyze audio bytes and return metadata."""
        result = {
            "format": "未知",
            "size": len(audio_bytes),
            "duration": None,
            "sample_rate": 0,
            "channels": 0,
        }

        # WAV detection: RIFF....WAVE
        if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            result["format"] = "WAV"
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                    result["sample_rate"] = wf.getframerate()
                    result["channels"] = wf.getnchannels()
                    frames = wf.getnframes()
                    if result["sample_rate"] > 0:
                        result["duration"] = frames / result["sample_rate"]
            except Exception:
                pass
        # MP3 detection: ID3 tag or MPEG sync word
        elif audio_bytes[:3] == b"ID3" or (len(audio_bytes) > 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
            result["format"] = "MP3"
            result["sample_rate"] = 24000  # MiMo default
            # Rough duration estimate: ~128kbps typical
            if len(audio_bytes) > 4:
                result["duration"] = len(audio_bytes) / (128 * 1024 / 8)
        # OGG detection
        elif audio_bytes[:4] == b"OggS":
            result["format"] = "OGG"
        # FLAC detection
        elif audio_bytes[:4] == b"fLaC":
            result["format"] = "FLAC"
        # M4A/AAC detection
        elif len(audio_bytes) > 12 and audio_bytes[4:8] == b"ftyp":
            result["format"] = "M4A/AAC"

        return result

    async def _extract_audio(self, event: AstrMessageEvent) -> Optional[bytes]:
        """Extract audio bytes from a message.

        Primary: use AstrBot's Record component (works with NapCat/aiocqhttp).
        Fallback: scan message segments for audio URL and download via HTTP.
        """
        # Path 1: AstrBot Record component
        msg_obj = getattr(event, "message_obj", None)
        msg_comps = getattr(msg_obj, "message", None) if msg_obj else None
        if msg_comps:
            for comp in msg_comps:
                is_record = (Record and isinstance(comp, Record)) or hasattr(comp, "convert_to_file_path")
                if is_record:
                    logger.info(f"Found Record component: file={getattr(comp,'file','')}, url={getattr(comp,'url','')}")
                    # Try convert_to_file_path first (NapCat get_record API)
                    try:
                        local_path = await comp.convert_to_file_path()
                        logger.info(f"convert_to_file_path result: {local_path}")
                        if local_path and os.path.exists(local_path):
                            with open(local_path, "rb") as f:
                                return f.read()
                    except Exception as e:
                        logger.warning(f"Record.convert_to_file_path failed: {e}")
                    # Try comp.file as local path directly
                    file_ref = getattr(comp, "file", None)
                    if file_ref and os.path.isfile(str(file_ref)):
                        try:
                            with open(str(file_ref), "rb") as f:
                                return f.read()
                        except Exception as e:
                            logger.warning(f"Failed to read local file {file_ref}: {e}")
                    # Try comp.url or comp.file as HTTP URL
                    url = getattr(comp, "url", None) or file_ref
                    if url and isinstance(url, str) and url.startswith("http"):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(url) as resp:
                                    if resp.status == 200:
                                        return await resp.read()
                        except Exception:
                            logger.warning(f"Failed to download record from {url}")

        # Path 2: Legacy dict-style attachments
        attachments = getattr(event, "attachments", None)
        if attachments:
            for att in attachments:
                url = None
                if isinstance(att, dict):
                    url = att.get("url") or att.get("data")
                    if isinstance(url, dict):
                        url = url.get("url")
                elif hasattr(att, "url"):
                    url = att.url
                if not url or not isinstance(url, str) or not url.startswith("http"):
                    continue
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                return await resp.read()
                except Exception:
                    logger.warning(f"Failed to download attachment from {url}")

        logger.warning("No audio Record or attachment found in message")
        return None

    def _save_temp_audio(self, audio_bytes: bytes, suffix: str) -> str:
        """Save audio bytes to a temp file. Raises PermissionError on write failure."""
        tmp_dir = Path(tempfile.gettempdir()) / "mimo_tts_clone"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=suffix, dir=str(tmp_dir))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
        except Exception:
            os.close(fd)
            raise
        return path

    @staticmethod
    def _cleanup_temp_file(path: str):
        """Delete a temp file if it exists."""
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass

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
