# KimiChat 插件说明

基于[国产大模型Kimi](https://kimi.moonshot.cn/)开发的[chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat)插件，支持联网对话、多种格式文件解析、图片识别、视频分析等功能。
插件基于 [cow_plugin_kimichat](https://github.com/LargeCupPanda/cow_plugin_kimichat) 修改，短视频链接处理基于[media_parser插件](https://github.com/5201213/media_parser)制作。

本插件代码全部由GPT生成，可能存在bug。欢迎通过Issue反馈问题或提出建议。

音频转写API来自硅基流动，可通过[邀请链接](https://cloud.siliconflow.cn/i/tPQSNa6I)注册。


## refresh_token位置
![image](https://github.com/user-attachments/assets/32deed4d-89a0-48ca-9083-e64c264f72db)


## 演示

![微信图片_20241217170034](https://github.com/user-attachments/assets/9ed3cc6e-0e22-4ffb-972a-131094a49a5b)

![2](https://github.com/user-attachments/assets/55739be9-fee1-449e-aca3-805e51d6b736)


## 🌟 主要功能

- 💬 智能对话：支持联网搜索和多轮上下文对话，提供类似ChatGPT的对话体验
- 📄 文件解析：支持多种格式文件的内容分析，包括文档、图片、视频、代码等
- 🖼️ 图片识别：支持图片内容识别和描述，可提取图片中的文字、物体、场景等信息（新增视觉思考k1模型）
- 🎥 视频分析：支持视频内容理解和自动总结，可提取视频中的关键信息和精华片段
- 🔗 链接总结：自动提取和总结分享链接的内容，方便快速了解链接要点
- 👥 场景支持：支持群聊和私聊两种场景，可根据不同场景提供个性化服务
- ✨ 自定义提示词：支持个性化配置提示词，可根据需求定制对话风格和任务

## 📦 快速安装

1. 替换`channel`目录下的文件：
   - 将`wechat_channel.py`和`wechat_message.py`两个文件复制到`chatgpt-on-wechat\channel\wechat`目录下，覆盖原有文件
   - 修改后的文件增加了对视频文件的接收和处理功能

2. 使用管理员权限安装插件：
   ```bash
   #installp https://github.com/single228758/cow_plugin_kimichat.git
   ```

3. 安装依赖：
   ```bash
   pip install requests moviepy pydub numpy
   ```

4. 配置插件：
   ```bash 
   cd plugins/cow_plugin_kimichat/
   vim config.json  # 修改配置参数
   ```

5. 启用插件：
   ```bash
   #scanp
   ```

## ⚙️ 核心配置

编辑 `config.json` 文件，必要配置项：

```json
{
    "refresh_token": "你的refresh_token",  // Kimi API令牌(必填)
    "keyword": "k",                        // 触发关键词
    "group_names": ["群名1", "群名2"],     // 启用自动总结的群名
    "auto_summary": true,                  // 是否开启自动总结
    "private_auto_summary": false          // 私聊是否自动总结
}
```

## 🎯 使用指南

### 基础对话
```
k 你好                    # 开始对话
k 今天广州天气如何?        # 联网搜索天气信息
kimi重置会话              # 重置对话上下文
k链接内容翻译中文https://.... #使用自定义提示词总结链接内容
```

### 文件识别
```
识别                      # 识别单个文件/图片内容
识别 3                    # 识别多个(这里为3个)文件/图片内容
识别 里面内容翻译中文      # 使用自定义提示词识别文件/图片，并翻译成中文
```

### 视频分析
```
视频                      # 分析视频内容并总结要点
视频 这两只猫在干嘛        # 使用自定义提示词分析视频内容
视频 =9.76 复制打开抖音，看看...  # 提取抖音视频链接并进行自动解析总结
```

### 链接解析
- 直接分享链接即可触发自动总结功能
- 需在`group_names`中配置启用自动总结的群组名称
- 如需在私聊中启用自动总结，请将`private_auto_summary`设为`true`

## 📝 支持的文件格式

- 文档：`.doc`, `.docx`, `.pdf`, `.txt`
- 图片：`.jpg`, `.png`, `.gif`, `.webp`
- 视频：`.mp4`, `.avi`, `.mov`, `.mkv`
- 代码：`.py`, `.java`, `.json`, `.html`

## ⚠️ 使用限制

- 普通文件大小：≤ 50MB
- 视频文件大小：≤ 100MB
- 视频帧提取：最多50帧
- 需正确配置`refresh_token`才能使用Kimi API
- 音频转写功能需额外配置`audio_token`

## 🔍 常见问题

1. API连接失败
   - 检查`refresh_token`是否有效，确保没有过期或错误
   - 确认网络连接正常，可以访问Kimi API服务器

2. 文件识别失败
   - 检查文件格式是否在支持列表中
   - 确认文件大小没有超过限制，普通文件≤50MB，视频文件≤100MB

3. 群聊无响应
   - 检查`group_names`配置，确保目标群名在列表中
   - 确认机器人已被正常拉入群聊，并具有发言权限

4. 音频转写报错
   - 检查是否已配置`audio_token`，并确保token有效
   - 确认音频文件格式和大小是否符合硅基流动的要求

## 🔄 版本历史

v0.2.1
- 📝 完善README文档，补充更多细节和说明
- 🐛 修复若干已知bug，提升插件稳定性

v0.2
- ✨ 新增视频分析功能，支持提取视频关键信息和自动总结
- 🔧 优化文件处理逻辑，提高大文件解析速度和成功率

v0.1 
- 🎉 实现基础对话功能，支持多轮上下文交互
- 🌐 接入联网搜索能力，支持实时查询和数据获取
- 💬 支持私聊及群聊，可根据场景提供定制化服务

## 🤝 贡献与反馈

如果您在使用过程中发现任何问题，或有任何建议和想法，欢迎通过Issue反馈。
同时也欢迎提交PR，贡献代码或改进文档，让KimiChat变得更好！

感谢您的支持和贡献！
```
