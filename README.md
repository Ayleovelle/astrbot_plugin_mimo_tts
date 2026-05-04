# MiMo VoiceClone TTS — AstrBot 语音克隆插件

基于 **小米 MiMo-V2.5-TTS-VoiceClone** 引擎的 AstrBot 文本转语音插件。只需几秒参考音频即可克隆任意音色，让你的聊天机器人用自定义声音"开口说话"。

## 功能特性

- **一键克隆音色** — 发送一段 3-10 秒的语音即可克隆，无需训练
- **多音色管理** — 支持保存、切换、删除多个克隆音色
- **风格控制** — 支持情绪（开心/温柔/疲惫）和方言（东北话/粤语/四川话）等
- **多格式输出** — 支持 WAV / MP3 / PCM16 音频格式
- **跨平台** — 兼容 QQ、Telegram、Discord、KOOK、钉钉、飞书等 10+ 平台

## 安装

### 1. 获取 API Key

前往 [小米 MiMo 开放平台](https://platform.xiaomimimo.com/#/console/api-keys) 注册并获取 API Key（公测期间免费）。

### 2. 安装插件

将本仓库克隆到 AstrBot 的插件目录：

```bash
cd AstrBot/data/plugins/
git clone https://github.com/Ayleovelle/astrbot_plugin_mimo_tts.git
```

然后在 AstrBot WebUI → 插件管理 → 加载插件。

### 3. 配置 API Key

两种方式任选其一：

**方式一：环境变量**
```bash
export MIMO_API_KEY="your_api_key_here"
```

**方式二：命令配置**
```
/mimo_config apikey <你的API Key>
```

## 使用方法

### 克隆音色

发送一段语音消息（清唱/朗读均可，3-10 秒），同时附上命令：

```
/mimo_clone 我的声音
```

克隆成功后会返回一段测试语音，验证效果。

### 文字转语音

```
/mimo_tts 你好，这是一段用克隆音色合成的语音
```

### 设置语音风格

```
/mimo_style 开心          # 愉快的情绪
/mimo_style 东北话        # 东北方言
/mimo_style 粤语          # 粤语
/mimo_style 温柔但疲惫     # 细腻的情绪描述
/mimo_style none          # 清除风格
```

### 管理音色

```
/mimo_voices              # 查看已克隆的音色列表
/mimo_set_voice 我的声音   # 切换音色
/mimo_del_voice 旧音色     # 删除音色
```

### 查看配置

```
/mimo_config                          # 查看当前配置
/mimo_config apikey <新Key>           # 更换 API Key
/mimo_config format <wav|mp3|pcm16>   # 切换音频格式
```

## 命令速查

| 命令 | 功能 |
|------|------|
| `/mimo_tts <文本>` | 将文本转为语音 |
| `/mimo_clone <名称>` | 从音频附件克隆新音色 |
| `/mimo_voices` | 列出所有已克隆的音色 |
| `/mimo_set_voice <名称>` | 切换当前音色 |
| `/mimo_del_voice <名称>` | 删除指定音色 |
| `/mimo_style <风格>` | 设置情绪/方言风格 |
| `/mimo_config [key] [value]` | 查看/修改配置 |

## 工作原理

```
用户音频 → 本地缓存(去重) → base64编码 → MiMo API → 合成语音 → 返回音频消息
```

每次 TTS 请求将缓存的参考音频随文本一起发送给 MiMo API，API 在目标音色下合成语音并返回。

## 常见问题

**Q: 支持哪些音频格式作为参考？**
WAV、MP3 均可，建议 3-10 秒，发音清晰即可。

**Q: 需要付费吗？**
MiMo API 当前处于公测期，免费使用。后续收费政策请关注官方公告。

**Q: 参考音频会占用多少空间？**
每个音色约 100-500KB，本地存储于插件 data 目录下的 `voices/` 文件夹。

**Q: 能在多个平台同时使用吗？**
可以，AstrBot 支持的所有平台均已适配。

**Q: 支持流式输出吗？**
当前版本为非流式合成。后续版本计划支持 PCM16 流式输出以降低首字延迟。

## 依赖

- Python >= 3.10
- AstrBot >= 4.16
- openai >= 1.30.0
- aiohttp >= 3.9.0

## 许可

MIT License
