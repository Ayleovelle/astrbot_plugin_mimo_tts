<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A54.16-green)
![Version](https://img.shields.io/badge/version-1.0.0-orange)
![License](https://img.shields.io/badge/license-MIT-blue)
![Status](https://img.shields.io/badge/状态-活跃开发中-brightgreen)

</div>

# 🎤 MiMo VoiceClone TTS

基于 **小米 MiMo-V2.5-TTS-VoiceClone** 引擎的 AstrBot 语音克隆插件。

只需 **几秒参考音频** 即可克隆任意音色，让你的聊天机器人用自定义声音「开口说话」。

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🎯 **一键克隆** | 发送 3-10 秒语音即可克隆，无需训练、无需等待 |
| 🗂️ **多音色管理** | 支持保存、切换、删除多个音色，随时更换 |
| 🎭 **风格控制** | 支持情绪描述（开心/温柔/疲惫）与方言（东北话/粤语/四川话） |
| 🔊 **多格式输出** | WAV / MP3 / PCM16 自由切换 |
| 🌐 **跨平台兼容** | QQ、Telegram、Discord、KOOK、钉钉、飞书等 10+ 平台 |
| 📦 **轻量去重** | SHA256 指纹去重，重复音频自动识别 |

---

## 📂 项目结构

```
astrbot_plugin_mimo_tts/
├── main.py                # 插件入口，7 个命令处理器
├── mimo_client.py         # MiMo API 客户端（OpenAI SDK 封装）
├── voice_manager.py       # 音色 CRUD + 本地缓存
├── config_manager.py      # 配置持久化（Key / 格式 / 风格）
├── metadata.yaml          # AstrBot 插件元数据
├── requirements.txt       # Python 依赖
├── README.md              # 项目文档
└── .gitignore
```

---

## 🚀 快速开始

### 1. 获取 API Key

前往 [小米 MiMo 开放平台](https://platform.xiaomimimo.com/#/console/api-keys) 注册获取 API Key。

> 公测期间 **免费**，无需付费即可使用。

### 2. 安装插件

```bash
cd AstrBot/data/plugins/
git clone https://github.com/Ayleovelle/astrbot_plugin_mimo_tts.git
```

然后在 AstrBot **WebUI → 插件管理** 中加载插件。

### 3. 配置 API Key

```bash
# 方式一：环境变量
export MIMO_API_KEY="your_api_key_here"

# 方式二：聊天框命令
/mimo_config apikey <你的Key>
```

### 4. 开始使用

```
/mimo_clone 我的声音          ← 发送语音消息克隆音色
/mimo_tts 你好世界             ← 用克隆音色合成语音
```

---

## 📖 命令参考

### 🎯 核心功能

| 命令 | 说明 | 示例 |
|------|------|------|
| `/mimo_clone <名称>` | 克隆新音色（需附带音频） | `/mimo_clone 小明的声音` |
| `/mimo_tts <文本>` | 将文本转为语音 | `/mimo_tts 今天天气真好` |

### 🗂️ 音色管理

| 命令 | 说明 | 示例 |
|------|------|------|
| `/mimo_voices` | 列出所有已克隆的音色 | `/mimo_voices` |
| `/mimo_set_voice <名称>` | 切换当前使用的音色 | `/mimo_set_voice 小美` |
| `/mimo_del_voice <名称>` | 删除指定音色 | `/mimo_del_voice 旧音色` |

### 🎭 风格与配置

| 命令 | 说明 | 示例 |
|------|------|------|
| `/mimo_style <描述>` | 设置情绪/方言风格 | `/mimo_style 东北话` |
| `/mimo_style none` | 清除风格设置 | `/mimo_style none` |
| `/mimo_config` | 查看当前配置 | `/mimo_config` |
| `/mimo_config apikey <Key>` | 更换 API Key | `/mimo_config apikey sk-xxx` |
| `/mimo_config format <fmt>` | 切换音频格式 | `/mimo_config format mp3` |

---

## 🔄 工作流程

```mermaid
graph LR
    A[用户音频] --> B[SHA256 去重]
    B --> C[本地缓存]
    C --> D[base64 编码]
    D --> E[MiMo API]
    E --> F[合成语音]
    F --> G[返回音频消息]
```

---

## 🛠 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 框架 | AstrBot >= 4.16 |
| API | Xiaomi MiMo-V2.5-TTS-VoiceClone |
| HTTP | OpenAI SDK (`openai>=1.30`) |
| 异步 | aiohttp >= 3.9 |
| 音频 | WAV / MP3 / PCM16（24kHz） |

---

## ❓ 常见问题

<details>
<summary><b>支持哪些音频格式作为参考？</b></summary>

WAV、MP3 均可，建议 3-10 秒、发音清晰即可。支持语音消息和音频文件两种上传方式。
</details>

<details>
<summary><b>需要付费吗？</b></summary>

MiMo API 当前处于 **公测期免费**，后续收费政策请关注 [小米 MiMo 开放平台](https://platform.xiaomimimo.com) 公告。
</details>

<details>
<summary><b>克隆的音色保存在哪里？</b></summary>

参考音频缓存于插件 `data/voices/` 目录，每个音色约 100-500KB。元数据索引存储在 `mimo_tts_voices.json`。
</details>

<details>
<summary><b>支持流式输出吗？</b></summary>

当前版本为非流式合成。后续版本计划支持 PCM16 流式输出以降低首字延迟。
</details>

<details>
<summary><b>为什么提示「未检测到音频附件」？</b></summary>

请在发送 `/mimo_clone` 命令的**同一条消息中**附带语音或音频文件。不同平台的附件获取方式存在差异，如遇问题请提 Issue。
</details>

---

## 📄 许可

MIT License © 2026 Ayleovelle

---

<div align="center">

[⭐ Star](https://github.com/Ayleovelle/astrbot_plugin_mimo_tts) · [🐛 Issue](https://github.com/Ayleovelle/astrbot_plugin_mimo_tts/issues) · [🔌 AstrBot 插件](https://docs.astrbot.app/dev/star/plugin-new.html)

</div>
