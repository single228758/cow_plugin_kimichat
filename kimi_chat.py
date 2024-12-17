# coding=utf-8
"""
Author: chazzjimel
Email: chazzjimel@gmail.com
wechat：cheung-z-x

Description:

"""
import os
import json
import time
import logging
import re
import mimetypes
import shutil
import concurrent.futures
import requests
from pydub import AudioSegment
import subprocess
from moviepy import VideoFileClip
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from plugins import *
from .module.token_manager import tokens, refresh_access_token
from .module.api_models import create_new_chat_session, stream_chat_responses
from .module.file_uploader import FileUploader
from .module.video_frame_manager import VideoFrameManager
from .module.spjx.media_parser import MediaParser


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

@plugins.register(
    name="KimiChat",
    desire_priority=1,
    hidden=True,
    desc="kimi模型对话",
    version="0.2",
    author="chazzjimel",
)
class KimiChat(Plugin):
    def __init__(self):
        super().__init__()
        try:
            # 加载配置文件
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            if not os.path.exists(config_path):
                raise Exception("配置文件不存在")
            
            with open(config_path, "r", encoding="utf-8") as f:
                self.conf = json.load(f)
            
            # 从配置文件读取 upload_threads 参数
            upload_threads = self.conf.get("upload_threads", 5)
            
            # 使用 upload_threads 初始化线程池  
            self.executor = ThreadPoolExecutor(max_workers=upload_threads)
            
            # 确保 tmp 目录存在
            if not os.path.exists('tmp'):
                os.makedirs('tmp')
                logger.info("[KimiChat] 创建 tmp 目录")
            
            # 统一文件存储目录
            self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
            self.storage_dir = os.path.join(self.plugin_dir, 'storage')
            
            # 创建存储目录结构
            self.temp_dir = os.path.join(self.storage_dir, 'temp')  # 临时文件目录
            self.video_dir = os.path.join(self.storage_dir, 'video')  # ��频处理目录
            self.frames_dir = os.path.join(self.video_dir, 'frames')  # 视频帧目录
            
            # 创建所需目录
            for dir_path in [self.storage_dir, self.temp_dir, self.video_dir, self.frames_dir]:
                if not os.path.exists(dir_path):
                    os.makedirs(dir_path)
                    logger.info(f"[KimiChat] 创建目录: {dir_path}")
            
            # 初始化时清理所有临时文件
            self.clean_storage()
            
            # 设置日志编码
            import sys
            if sys.stdout.encoding != 'utf-8':
                import codecs
                sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
                sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
            
            # 设置日志
            log_config = self.conf.get("logging", {})
            if not log_config.get("enabled", True):
                logger.disabled = True
            else:
                logger.setLevel(log_config.get("level", "INFO"))
            
            # 从配置文件加载所有设置
            tokens['refresh_token'] = self.conf["refresh_token"]
            if not tokens['access_token']:
                refresh_access_token()
            
            # 基础设��
            self.keyword = self.conf["keyword"]
            self.reset_keyword = self.conf["reset_keyword"] 
            
            # 群组配置
            self.group_names = self.conf["group_names"]
            self.allowed_groups = self.conf.get("allowed_groups", [])
            self.auto_summary = self.conf["auto_summary"]
            self.summary_prompt = self.conf["summary_prompt"]
            self.exclude_urls = self.conf["exclude_urls"]
            
            # 文件处理配置
            self.file_upload = self.conf["file_upload"]
            self.file_triggers = self.conf["file_triggers"]
            self.file_parsing_prompts = self.conf["file_parsing_prompts"]
            self.image_prompts = self.conf["image_prompts"]
            self.use_system_prompt = self.conf["use_system_prompt"]
            self.show_custom_prompt = self.conf["show_custom_prompt"]
            
            # 其他初始化
            self.waiting_files = {}
            self.chat_data = {}
            self.processed_links = {}
            self.link_cache_time = 60  # 链接缓存时间（秒）
            
            # 注册事件处理器
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            
            # 根据配置决定是否显示初始化信息
            if log_config.get("show_init_info", True):
                logger.info("[KimiChat] ---- 插件初始化信息 ----")
                logger.info(f"[KimiChat] 关键词: {self.keyword}")
                logger.info(f"[KimiChat] 群组列表: {self.group_names}")
                logger.info(f"[KimiChat] 文件触发词: {self.file_triggers}")
                logger.info("[KimiChat] 初始化完成")
            
            # 初始化视频配置
            video_config = self.conf.get("video_config", {})
            self.video_triggers = video_config.get("trigger_keywords", ["视频", "视频分析"])
            
            # 将视频触发词添加到文件触发词列表中
            self.file_triggers.extend(self.video_triggers)
            
            self.video_save_dir = os.path.join(os.path.dirname(__file__), 'video')
            if not os.path.exists(self.video_save_dir):
                os.makedirs(self.video_save_dir)
                logger.info(f"[KimiChat] 创建视频保存目录: {self.video_save_dir}")
            
            self.frame_interval = video_config.get("frame_interval", 1.0)
            self.max_frames = video_config.get("max_frames", 50)
            self.video_summary_prompt = video_config.get("summary_prompt", "")
            self.supported_video_formats = video_config.get("supported_formats", 
                [".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv"])
            
            # 初始化 MediaParser
            self.media_parser = MediaParser(self.conf)
            
            # 添加视频处理状态
            self.waiting_video_links = {}
            
            # 启动定期清理任务
            self.start_cleanup_task()
            
            # 从环境变量或配置文件读取参数
            self.max_frames = int(os.environ.get("KIMI_MAX_FRAMES", 50))
            self.audio_wait_time = int(os.environ.get("KIMI_AUDIO_WAIT_TIME", 60))
            
        except Exception as e:
            logger.error(f"[KimiChat] 初始化失败: {str(e)}", exc_info=True)
            raise e

        # 添加会话管理相关属性
        self.chat_sessions = {}  # 格式: {session_key: {'chat_id': chat_id, 'last_active': timestamp}}

    def __del__(self):
        """清理资源"""
        try:
            # 关闭线程池
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=False)
            # 清理其他资源...
        except Exception as e:
            logger.error(f"[KimiChat] 关闭资源失败: {e}")

    def check_file_format(self, file_path):
        """检查文件格式是否支持"""
        if not file_path:
            return False
        
        # 获取文件扩展名
        ext = os.path.splitext(file_path)[1].lower()
        
        # 如果是视频文件,使用视频格式列表检查
        if ext in self.supported_video_formats:
            return True
        
        # 从配置文件获取支持的格式列表
        supported_formats = self.conf.get("supported_file_formats", [
            ".dot", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".ppa", ".pptx",
            ".md", ".pdf", ".txt", ".csv",
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
            ".py", ".java", ".cpp", ".c", ".h", ".hpp", ".js", ".ts", ".html", ".css",
            ".json", ".xml", ".yaml", ".yml", ".sh", ".bat",
            ".log", ".ini", ".conf", ".properties"
        ])
        
        # 检查扩展名是否在支持列表中
        is_supported = ext in supported_formats
        
        # 添加日志输出以便调试
        if not is_supported:
            logger.warning(f"[KimiChat] 文件格式检查: 扩展名={ext}, 是否支持={is_supported}")
            logger.debug(f"[KimiChat] 支持的格式列表: {supported_formats}")
        
        return is_supported

    def get_valid_file_path(self, content):
        """获取有效的文件路径"""
        # 查文件路径
        file_paths = [
            content,  # 原始路径
            os.path.abspath(content),  # 绝对路径
            os.path.join('tmp', os.path.basename(content)),  # tmp目录
            os.path.join(os.getcwd(), 'tmp', os.path.basename(content)),  # 完整tmp目录
            os.path.join(self.temp_dir, os.path.basename(content)),  # 临时目录
            os.path.join('plugins/cow_plugin_kimichat/video', os.path.basename(content)),  # 视频目录
        ]
        
        for path in file_paths:
            logger.debug(f"[KimiChat] 尝试路径: {path}")
            if os.path.isfile(path):
                logger.debug(f"[KimiChat] 找到文件: {path}")
                return path
        
        return None

    def handle_url_content(self, url, custom_prompt, user_id, e_context):
        """处理URL内容"""
        try:
            # 使用MediaParser提取链接信息
            title, url = self.media_parser.extract_share_info(url)
            if not url:
                return False
            
            # 检查是否在排除列表中
            if any(exclude_url in url for exclude_url in self.exclude_urls):
                return False
            
            # 获取或创建会话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }

            # 格式化URL为Kimi格式
            formatted_url = f'<url id="" type="url" status="" title="" wc="">{url}</url>'

            # 构建提示词
            if custom_prompt:
                prompt = custom_prompt
            else:
                prompt = self.summary_prompt

            # 使用提示词和链接获取总结
            rely_content = stream_chat_responses(
                chat_id=chat_id,
                content=f"{formatted_url}\n\n{prompt}",
                refs=[],
                use_search=True,
                extend={"sidebar": True}
            )

            if rely_content:
                # 格式化输出
                formatted_content = ""
                
                # 如果有标题添加标题
                if title:
                    formatted_content += f"【标题】{title}\n\n"
                
                # 如��有自义提示词，显示提示词
                if custom_prompt and self.show_custom_prompt:
                    formatted_content += f"【提示词】{custom_prompt}\n\n"
                
                formatted_content += rely_content
                
                # 添加提示信息
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, formatted_content + tip_message)
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            else:
                reply = Reply(ReplyType.TEXT, "链接解析失败，请重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

        except Exception as e:
            logger.error(f"[KimiChat] 处理URL内容失败: {e}")
            reply = Reply(ReplyType.TEXT, "处理链接时出错，请重试")
            e_context["channel"].send(reply, e_context["context"])
            e_context.action = EventAction.BREAK_PASS
            return True

    def on_handle_context(self, e_context: EventContext):
        """处理消息上下文"""
        if not e_context['context'].content:
            return

        content = e_context['context'].content.strip()
        context_type = e_context['context'].type
        
        # 获取用信息
        msg = e_context['context'].kwargs.get('msg')
        is_group = e_context['context'].kwargs.get('isgroup', False)
        
        # 获取正确的用户ID和群组信息
        if is_group:
            group_id = msg.other_user_id if msg else None
            real_user_id = msg.actual_user_id if msg and msg.actual_user_id else msg.from_user_id
            waiting_id = f"{group_id}_{real_user_id}"
            group_name = msg.other_user_nickname if msg else None
            
            # 检查是否是分享类型消息
            if context_type == ContextType.SHARING:
                # 检查是否是视频链接
                if self.media_parser.is_video_share(content):
                    # 获视频触发词列表
                    video_triggers = self.conf.get("video_config", {}).get("trigger_keywords", ["视频", "视频分析"])
                    # 检查是否在等待视频状态或是通过视频触发词触发
                    if waiting_id not in self.waiting_video_links and not any(content.startswith(trigger) for trigger in video_triggers):
                        return  # 自动处理视频链接
                    return self.handle_video_share(content, waiting_id, e_context)
                # 其他类型的分享链接
                elif self.auto_summary and group_name in self.group_names:
                    logger.info(f"[KimiChat] 到群 {group_name} 的链接分享: {content}")
                    return self.handle_url_content(content, None, real_user_id, e_context)
            
            # 对于其他功能，检查allowed_groups
            # 如果allowed_groups为空，则允许所有群使用
            elif self.allowed_groups and group_id not in self.allowed_groups:
                return
        else:
            real_user_id = msg.from_user_id if msg else None
            waiting_id = real_user_id
            group_name = None

        # 处理重置会话命令
        if context_type == ContextType.TEXT:
            # 使用配置文件中的重置关键词
            reset_keyword = self.conf.get("reset_keyword", "kimi重置会话")
            if content.strip() == reset_keyword:
                logger.info(f"[KimiChat] 用户 {real_user_id} 请求重置会话")
                success, reply_text = self.reset_chat(real_user_id, e_context['context'])
                reply = Reply(ReplyType.TEXT, reply_text)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True

        # 处理文本息中的视频链接 - 仅当在等待视频状态时
        if context_type == ContextType.TEXT and waiting_id in self.waiting_video_links:
            # 检查否包含视频分享链接
            if self.media_parser.is_video_share(content):
                logger.info(f"[KimiChat] 检测到视频分享链接: {content}")
                result = self.handle_video_share(content, waiting_id, e_context)
                # 清理等待状态
                if waiting_id in self.waiting_video_links:
                    del self.waiting_video_links[waiting_id]
                return result
            return

        # 处理文件上传 - 仅当在等待文件状态时
        if context_type in [ContextType.FILE, ContextType.IMAGE]:
            if waiting_id in self.waiting_files:
                logger.info(f"[KimiChat] 接收到文件: type={context_type}, user={waiting_id}")
                
                # 准备文件
                file_path = self.prepare_file(msg)
                if not file_path:
                    reply = Reply(ReplyType.TEXT, "文件准备失败，请重试")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                
                # 检查文件格式
                if not self.check_file_format(file_path):
                    reply = Reply(ReplyType.TEXT, "不支持的文件格式")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                
                # 处理文件
                waiting_info = self.waiting_files[waiting_id]
                custom_prompt = waiting_info.get('prompt')
                
                return self.handle_file_recognition(file_path, waiting_id, e_context, custom_prompt)
            return

        # 处理视频消息 - 仅当在等待视频状态时
        if context_type == ContextType.VIDEO:
            if waiting_id in self.waiting_video_links:
                # 发送接收提示
                receive_reply = Reply(ReplyType.TEXT, "视频接收完毕，正在解析处理中...")
                e_context["channel"].send(receive_reply, e_context["context"])
                
                # 准备视频文件
                file_path = self.prepare_file(msg)
                if not file_path:
                    reply = Reply(ReplyType.TEXT, "视频文件准备失败，请重试")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                    
                # 获取自定义提示词
                custom_prompt = self.waiting_video_links[waiting_id].get('custom_prompt')
                
                # 处理视频
                result = self.process_video_file(file_path, waiting_id, e_context, custom_prompt)
                # 清理等待状态
                if waiting_id in self.waiting_video_links:
                    del self.waiting_video_links[waiting_id]
                return result
            return

        # 处理文本消息 - 仅当触发关键词时
        if context_type == ContextType.TEXT:
            content = content.strip()
            
            # 检查是否是文件识别触发词
            for trigger in self.file_triggers:
                if content.startswith(trigger):
                    logger.info(f"[KimiChat] 用户 {real_user_id} 触发文件识别")
                    return self.handle_file_trigger(trigger, content, real_user_id, e_context)
            
            # 检查是否是视频触发词
            video_triggers = self.conf.get("video_config", {}).get("trigger_keywords", ["视频", "视频分析"])
            if content in video_triggers:
                logger.info(f"[KimiChat] 用户 {real_user_id} 触发视频处理")
                # 设置等待视频状态
                self.waiting_video_links[waiting_id] = {
                    'trigger_time': time.time(),
                    'timeout': self.conf.get("file_timeout", 300)
                }
                reply = Reply(ReplyType.TEXT, "请发送需要识别的视频或视频分享链接")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True

            # 处理普通文本对话 - 仅当以关键词开头时
            if self.keyword and content.startswith(self.keyword):
                # 移除关键词前缀
                msg = content[len(self.keyword):].strip() if content.startswith(self.keyword) else content
                return self.handle_normal_chat(msg, real_user_id, e_context)

        return False  # 如果不是我们要处理的消息，返回False让其他插件处理

    def clean_references(self, text):
        """清理引用记"""
        if not text:
            return text
        # 移除引用记
        text = re.sub(r'\[\^\d+\^\]', '', text)
        # 参考文献分
        text = re.sub(r'参考文献：[\s\S]*$', '', text)
        return text.strip()

    def handle_files(self, file_list, user_id, e_context):
        """处理多文件上传"""
        try:
            chat_id = create_new_chat_session()
            file_ids = []
            
            # 1. 批量上传所有件
            uploader = FileUploader()
            for file_info in file_list:
                try:
                    file_path = file_info['path']
                    file_id = uploader.upload(
                        os.path.basename(file_path),
                        file_path
                    )
                    if file_id:
                        file_ids.append(file_id)
                except Exception as e:
                    logger.error(f"[KimiChat] 文件上传失败: {str(e)}")
                    continue
            
            if not file_ids:
                raise Exception("没有文件上传成功")
                
            # 2. 等待所有文件解析完
            for _ in range(30):  # 最多等待30秒
                parse_response = requests.post(
                    "https://kimi.moonshot.cn/api/file/parse_process",
                    json={"ids": file_ids}
                )
                if all(f["status"] == "parsed" for f in parse_response.json()):
                    break
                time.sleep(1)
            
            # 3. 检查token大小
            token_response = requests.post(
                f"https://kimi.moonshot.cn/api/chat/{chat_id}/token_size",
                json={
                    "refs": file_ids,
                    "content": ""
                }
            )
            
            if token_response.json().get("over_size"):
                raise Exception("文件容过大")
            
            # 发送息
            rely_content = stream_chat_responses(
                chat_id=chat_id,
                content=self.file_parsing_prompts,
                refs=file_ids
            )
            
            if rely_content:
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
            else:
                reply = Reply(ReplyType.TEXT, "件分析败，请重试")
            
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
            return True
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True

    def prepare_file(self, msg):
        """准备文件，确保载完成"""
        try:
            # 确保文已下载
            if hasattr(msg, '_prepare_fn') and not msg._prepared:
                msg._prepare_fn()
                msg._prepared = True
                time.sleep(1)  # 等待文准备完成
            
            # 获取原始文件路径
            original_path = msg.content
            if not original_path:
                logger.error("[KimiChat] 文件路径为空")
                return None
                
            # 获取有效的文件路径
            file_path = self.get_valid_file_path(original_path)
            if not file_path:
                logger.error(f"[KimiChat] 无法找到文件: {original_path}")
                return None
                
            # 生成唯一文名(使用一个时间戳)
            timestamp = int(time.time())
            filename = f"{timestamp}_{os.path.basename(file_path)}"
            temp_path = os.path.join(self.temp_dir, filename)
            
            # 如果源文件和目标文件是同一个文件才复制
            if os.path.abspath(file_path) != os.path.abspath(temp_path):
                shutil.copy2(file_path, temp_path)
                logger.info(f"[KimiChat] 文件已复制到: {temp_path}")
            else:
                logger.info(f"[KimiChat] 文在临时目录中: {temp_path}")
            
            return temp_path
            
        except Exception as e:
            logger.error(f"[KimiChat] 准备文件失败: {str(e)}")
            return None

    def process_file(self, file_path, user_id, e_context):
        """处理传的文件"""
        try:
            # 发送接收确认
            receive_reply = Reply(ReplyType.TEXT, "文件接收完毕，正在处理中...")
            e_context["channel"].send(receive_reply, e_context["context"])

            # 获取文件类型
            file_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            
            if file_type.startswith("video"):
                # 发送视频理提示
                video_reply = Reply(ReplyType.TEXT, "正在处理视频，这可能需要一点时间...")
                e_context["channel"].send(video_reply, e_context["context"])
                return self.process_video_file(file_path, user_id, e_context)
            else:
                # 发送文件处理提示
                process_reply = Reply(ReplyType.TEXT, "正在分析文件内容...")
                e_context["channel"].send(process_reply, e_context["context"])
                # 处理其他类型文件的逻辑...

        except Exception as e:
            logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True

    def handle_normal_chat(self, content, user_id, e_context):
        """处理普通对话"""
        try:
            # 每次对话前清理临时文件
            self.clean_storage()
            
            # 修改: 检查是否是单字母k的情况
            if content == self.keyword:
                logger.debug("[KimiChat] 忽略单独的触发词")
                return False
            
            # 修改: 触发词
            msg = content[len(self.keyword):].strip() if content.startswith(self.keyword) else content
            if not msg:
                logger.debug("[KimiChat] 消息内容为空")
                return False
            
            logger.info(f"[KimiChat] 收到消息: {msg}")
            
            # 检查是否包含链接
            url_match = re.search(r'(https?://\S+)', msg)
            if url_match:
                url = url_match.group(1)
                # 提取自定义提示词
                custom_prompt = None
                if url_match.start() > 0:
                    custom_prompt = msg[:url_match.start()].strip()
                
                # 处理链接
                return self.handle_url_content(url, custom_prompt, user_id, e_context)
            
            # 获取或创建统一会话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
                rely_content = stream_chat_responses(chat_id, msg, use_search=True)
            else:
                chat_id = create_new_chat_session()
                rely_content = stream_chat_responses(chat_id, msg, new_chat=True)
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }
            
            rely_content = self.clean_references(rely_content)
            if rely_content:
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理消息错误: {str(e)}")
            reply = Reply(ReplyType.TEXT, f"处理失败: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True

    def process_files(self, user_id, e_context):
        """处理已接收的件"""
        try:
            files = self.waiting_files.get(user_id, [])
            if not files or len(files) < 2:  # 少需要元数据和一个文件
                logger.error(f"[KimiChat] 用户 {user_id} 有待处理的文件")
                return False
            
            # 获���自定义提示词文件列表
            metadata = files[0]  # 第一个元素是元据
            custom_prompt = metadata.get("custom_prompt")
            file_list = files[1:]  # 其他元素是文件息
            
            # 建新会话
            chat_id = create_new_chat_session()
            
            # 传文件获回复
            for file_info in file_list:
                try:
                    if not isinstance(file_info, dict) or "path" not in file_info:
                        logger.error(f"[KimiChat] 无效的文件信息: {file_info}")
                        continue
                    
                    file_path = file_info.get("path")
                    file_type = file_info.get("type", "application/octet-stream")
                    
                    if not file_path or not os.path.exists(file_path):
                        logger.error(f"[KimiChat] 文件不在: {file_path}")
                        continue
                    
                    # 根据文件类型选择提示词
                    if file_type.startswith("image"):
                        prompt = self.image_prompts
                    else:
                        prompt = self.file_parsing_prompts
                    
                    # 如果有定义提示词，用自定义提示词
                    if custom_prompt:
                        prompt = custom_prompt
                    
                    logger.info(f"[KimiChat] 上传文件 {file_path} 使用提示词: {prompt}")
                    
                    # 传文
                    file_uploader = FileUploader()
                    file_id = file_uploader.upload(
                        os.path.basename(file_path), 
                        file_path,
                        skip_notification=True
                    )
                    
                    if not file_id:
                        logger.error(f"[KimiChat] 文件 {file_path} 上传失败")
                        continue
                    
                    # 发送提示词和文件ID
                    rely_content = stream_chat_responses(chat_id, prompt, file_id)
                    
                    # 清理引用记
                    rely_content = self.clean_references(rely_content)
                    
                    if rely_content:
                        # 添加提示信息
                        tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                        reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                        e_context["reply"] = reply
                        e_context.action = EventAction.BREAK_PASS
                
                except Exception as e:
                    logger.error(f"[KimiChat] 处理文件 {file_path if 'file_path' in locals() else '未知'} 出错: {str(e)}")
                    continue
                    
            # 理临时文件和记录
            self.clean_waiting_files(user_id)
            return True
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
            self.clean_waiting_files(user_id)
            return False

    def handle_file_recognition(self, file_path, user_id, e_context, custom_prompt=None):
        """处理文件识别"""
        try:
            logger.info(f"[KimiChat] 开始处理: {file_path}")
            
            # 获取等待信息
            waiting_info = self.waiting_files.get(user_id, {})
            expected_count = waiting_info.get('count', 1)
            
            # 将文件路径添加到接收列表
            if 'received_files' not in waiting_info:
                waiting_info['received_files'] = []
            waiting_info['received_files'].append(file_path)
            
            # 如果还没到所有文件，继续等待
            if len(waiting_info['received_files']) < expected_count:
                logger.info(f"[KimiChat] 已接收 {len(waiting_info['received_files'])}/{expected_count} 个文件")
                return True
                
            # 收到所有文件后，发送处理提示
            process_reply = Reply(ReplyType.TEXT, "文件接收完毕，正在解析处理中...")
            e_context["channel"].send(process_reply, e_context["context"])
            
            logger.info(f"[KimiChat] 开始处理 {expected_count} 个文件")
            
            # 获取或创建会话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }

            # 上传所有文件
            file_uploader = FileUploader()
            file_ids = []
            
            for file_path in waiting_info['received_files']:
                try:
                    file_id = file_uploader.upload(
                        os.path.basename(file_path),
                        file_path,
                        skip_notification=True
                    )
                    if file_id:
                        file_ids.append(file_id)
                except Exception as e:
                    logger.error(f"[KimiChat] 文件上传失败: {str(e)}")
                    continue
            
            if not file_ids:
                raise Exception("没有文件上传成功")

            # 根据第一个文件类型选择提示词
            first_file_type = mimetypes.guess_type(waiting_info['received_files'][0])[0] or "application/octet-stream"
            if first_file_type.startswith("image"):
                prompt = custom_prompt or self.image_prompts
            else:
                prompt = custom_prompt or self.file_parsing_prompts

            logger.info(f"[KimiChat] 使用提示词: {prompt}")
            
            # 发送提示词和所有文件ID
            rely_content = stream_chat_responses(
                chat_id=chat_id,
                content=prompt,
                refs=file_ids
            )
            
            # 清理引用标记
            rely_content = self.clean_references(rely_content)

            if rely_content:
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
            else:
                reply = Reply(ReplyType.TEXT, "处理失败，请重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
            
            # 清理等待状态和临时文件
            self.clean_waiting_files(user_id)
            return True

        except Exception as e:
            logger.error(f"[KimiChat] 处理文件识别失败: {e}")
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["channel"].send(reply, e_context["context"])
            e_context.action = EventAction.BREAK_PASS
            # 清理等待状态和临时文件
            self.clean_waiting_files(user_id)
            return True

    def process_waiting_files(self, user_id, e_context):
        """处理等待中的文件"""
        try:
            if user_id not in self.waiting_files:
                return False
            
            waiting_info = self.waiting_files[user_id]
            
            # 检查理否超时
            if time.time() - waiting_info['trigger_time'] > waiting_info['timeout']:
                logger.warning(f"[KimiChat] 文件处理时: {user_id}")
                self.clean_waiting_files(user_id)
                reply = Reply(ReplyType.TEXT, "文件处理超,请重上传")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 其余处理辑保持变
            ...
        except Exception as e:
            logger.error(f"[KimiChat] 处理等待文件出错: {str(e)}")
            self.clean_waiting_files(user_id)
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        
        return False

    def clean_waiting_files(self, user_id):
        """清理用户的临时文件和等待记录"""
        try:
            if user_id in self.waiting_files:
                waiting_info = self.waiting_files[user_id]
                # 清理所有接收的文件
                for file_path in waiting_info.get('received_files', []):
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            logger.debug(f"[KimiChat] 删除临时文件: {file_path}")
                        except Exception as e:
                            logger.error(f"[KimiChat] 删除临时文件失败: {file_path}, 错误: {str(e)}")

                # 删除等待状态
                del self.waiting_files[user_id]
                logger.debug(f"[KimiChat] 已清理等待状态: {user_id}")

        except Exception as e:
            logger.error(f"[KimiChat] 清理等待状态失败: {str(e)}")
            # 确保即使出错也删除等待状态
            if user_id in self.waiting_files:
                del self.waiting_files[user_id]

    def handle_file_trigger(self, trigger, content, user_id, e_context):
        """处理件触发"""
        # 获取用户信息
        msg = e_context['context'].kwargs.get('msg')
        is_group = e_context["context"].kwargs.get('isgroup', False)
        
        # 获取正确的用户ID
        if is_group:
            group_id = msg.other_user_id if msg else None
            real_user_id = msg.actual_user_id if msg and msg.actual_user_id else msg.from_user_id
            waiting_id = f"{group_id}_{real_user_id}"
        else:
            real_user_id = msg.from_user_id if msg else user_id
            waiting_id = real_user_id
            
        logger.info(f"[KimiChat] 用户 {real_user_id} 触发文件处理, 触发词: {trigger}")
        
        # 检查是否是视频触发词
        video_triggers = self.conf.get("video_config", {}).get("trigger_keywords", [])
        if trigger in video_triggers:
            # 提取视频触发词后面的内容
            remaining = content[len(trigger):].strip()
            
            # 检查是否包含视频分享链接
            url_match = re.search(r'(https?://\S+)', remaining) if remaining else None
            
            if url_match:
                # 如果链接前有文本，且不是链接标题（通常包含在分享文本中），则视为自定义提示词
                pre_text = remaining[:url_match.start()].strip()
                if pre_text and not any(keyword in pre_text.lower() for keyword in ['复制打开', '看看', '作品']):
                    custom_prompt = pre_text
                else:
                    custom_prompt = None
                return self.handle_video_share(url_match.group(1), waiting_id, e_context, custom_prompt)
            else:
                # 如果没有链接，则视为自定义提示词
                custom_prompt = remaining if remaining else None
                # 设置等待视频状态，包含��定义提示词
                self.waiting_video_links[waiting_id] = {
                    'trigger_time': time.time(),
                    'timeout': self.conf.get("file_timeout", 300),
                    'custom_prompt': custom_prompt
                }
                reply = Reply(ReplyType.TEXT, "请发送需要识别的视频或视频分享链接")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
        
        # 如果有完成任务,先清理掉
        if waiting_id in self.waiting_files:
            self.clean_waiting_files(waiting_id)
        
        # 解析文件数量和自定义提示词
        remaining = content[len(trigger):].strip()
        file_count = 1
        custom_prompt = None
        
        # 检查是否指定了文件数
        match = re.match(r'(\d+)\s*(.*)', remaining)
        if match:
            file_count = int(match.group(1))
            custom_prompt = match.group(2).strip() if match.group(2) else None
        else:
            custom_prompt = remaining if remaining else None
        
        # 从置取最大文件数限制
        max_files = self.conf.get("max_file_size", 50)  # 默认50
        if file_count > max_files:
            reply = Reply(ReplyType.TEXT, f"最多支持同时上传{max_files}个文件")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        
        # 确定文件型
        file_type = 'file'  # 默认类型
        
        # 配置获取视频触发词
        video_triggers = self.conf.get("video_config", {}).get("trigger_keywords", ["视频", "视频分析"])
        
        # 只需要判断是否是视频触发词
        if trigger in video_triggers:
            file_type = 'video'
        
        # 获取超时时间(分钟)
        timeout_minutes = self.conf.get("file_timeout", 300) // 60
        
        # 保存处理信息
        waiting_info = {
            'count': file_count,
            'received': [],
            'received_files': [],
            'prompt': custom_prompt,
            'trigger_time': time.time(),
            'timeout': timeout_minutes * 60,  # 转换为秒
            'trigger_user_id': real_user_id,
            'is_group': is_group,
            'group_id': msg.other_user_id if is_group else None,
            'type': file_type
        }
        
        # 保存等待状态
        self.waiting_files[waiting_id] = waiting_info
        logger.debug(f"[KimiChat] 设置等待状态: waiting_id={waiting_id}, info={waiting_info}")
        
        # 根据文件���型返回对应提示文本
        if file_type == 'video':
            reply_text = "请发送需要识别的视频"
        else:
            reply_text = f"请在{timeout_minutes}分钟内发{file_count}个文件"
        
        reply = Reply(ReplyType.TEXT, reply_text)
        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS
        return True

    def get_session_key(self, user_id, context):
        """生成会话键值
        群聊: 使用群ID作为key，整个群共享一个会话
        私聊: 使用用ID为key，每个用户独立会话
        """
        if context.kwargs.get('isgroup', False):
            group_id = context.kwargs['msg'].other_user_id
            return f"group_{group_id}"
        return f"private_{user_id}"

    def get_or_create_session(self, user_id, context):
        """获取或创建会，确保会有效性"""
        try:
            session_key = self.get_session_key(user_id, context)
            
            # 检查现有会话是否有效
            if session_key in self.chat_sessions:
                chat_info = self.chat_sessions[session_key]
                
                # 验证会话是否有效
                try:
                    response = requests.post(
                        f"https://kimi.moonshot.cn/api/chat/{chat_info['chat_id']}/token_size",
                        json={"content": ""}
                    )
                    if response.status_code == 200:
                        # 会话有效，更新最后活动间
                        chat_info['last_active'] = time.time()
                        return chat_info
                    
                except Exception as e:
                    logger.warning(f"[KimiChat] 会话 {chat_info['chat_id']} 已失效，将创建新会话")
            
            # 创建新会话
            chat_id = create_new_chat_session()
            session = {
                'chat_id': chat_id,
                'last_active': time.time(),
                'use_search': True,
                'context_type': 'group' if context.kwargs.get('isgroup', False) else 'private'
            }
            self.chat_sessions[session_key] = session
            logger.info(f"[KimiChat] 创建新会话: key={session_key}, chat_id={chat_id}")
            return session
            
        except Exception as e:
            logger.error(f"[KimiChat] 获取或创建会话失败: {str(e)}")
            # 创建新会话作为后备方案
            chat_id = create_new_chat_session()
            session = {
                'chat_id': chat_id,
                'last_active': time.time(),
                'use_search': True,
                'context_type': 'group' if context.kwargs.get('isgroup', False) else 'private'
            }
            self.chat_sessions[session_key] = session
            return session

    def reset_chat(self, user_id, context):
        """重置用户会话"""
        try:
            session_key = self.get_session_key(user_id, context)
            
            # 创建新的会话
            chat_id = create_new_chat_session()
            if not chat_id:
                logger.error("[KimiChat] 创建新会话失败")
                return False, "创建新会话失败，请稍后重试"
            
            # 更新会话数据
            self.chat_sessions[session_key] = {
                'chat_id': chat_id,
                'last_active': time.time(),
                'use_search': True,
                'context_type': 'group' if context.kwargs.get('isgroup', False) else 'private'
            }
            
            # 使用配置文件中的关键词
            keyword = self.conf.get("keyword", "k")
            
            # 根据会话类型返回不同的提示信息
            if context.kwargs.get('isgroup', False):
                reply_text = "重置本群的对话，所有群成员将开始新的对话。"
            else:
                reply_text = "已重置与您的对话，我们可以开始新的交谈。"
            
            # 清理等待中的文件数据
            if user_id in self.waiting_files:
                self.clean_waiting_files(user_id)
            
            # 清理视频等待状态
            if user_id in self.waiting_video_links:
                del self.waiting_video_links[user_id]
            
            logger.info(f"[KimiChat] 已重置会话: {session_key}")
            
            # 添加使用提示
            reply_text += f"\n\n发送 {keyword}+问题 可以继续追问"
            
            return True, reply_text
            
        except Exception as e:
            logger.error(f"[KimiChat] 重置会话失败: {str(e)}")
            return False, "重置会话时出现错误，请稍重试"

    def handle_message(self, context):
        group_name = context.get("group_name")
        if group_name not in self.conf.get("allowed_groups", []):
            return  # 如果不在允许的群组列表中，直接返回
        
        # 继续处理他辑
        ...

    def check_video_format(self, file_path):
        """检查视频格式是否支持"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_video_formats

    def handle_video(self, video_path, user_id, e_context):
        """处理视频"""
        try:
            # 使用统一话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }
            
            # 其余视频处理逻辑...
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理视频失败: {str(e)}")
            return False

    def handle_image(self, image_path, user_id, e_context):
        """处理图片"""
        try:
            # 使用统一话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }
            
            # 其余图片处理逻辑...
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理图片出错: {str(e)}")
            return False

    def clean_storage(self, file_paths=None):
        """清理存储的文件
        Args:
            file_paths: 指定要清的文件路径表,为None时理所有临时文件
        """
        try:
            if file_paths:
                # 清理指定文件
                for path in file_paths:
                    if path and os.path.exists(path):
                        os.remove(path)
                        logger.debug(f"[KimiChat] 已删除文件: {path}")
            else:
                # 清理所有临时文��
                for root, _, files in os.walk(self.storage_dir):
                    for file in files:
                        try:
                            file_path = os.path.join(root, file)
                            os.remove(file_path)
                            logger.debug(f"[KimiChat] 已删除文件: {file_path}")
                        except Exception as e:
                            logger.error(f"[KimiChat] 删除文件失败: {file_path}, 错误: {str(e)}")
                        
        except Exception as e:
            logger.error(f"[KimiChat] 清理存储文件出错: {str(e)}")

    def clean_temp_directory(self):
        """清理临时目录中的所有文"""
        try:
            # 清理 temp 目录
            if os.path.exists(self.temp_dir):
                for filename in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, filename)
                    try:
                        if os.path.isfile(file_path):
                            # 检查文件是否超过1小时
                            if time.time() - os.path.getctime(file_path) > 3600:
                                os.remove(file_path)
                                logger.debug(f"[KimiChat] 已删除过期临时文件: {file_path}")
                    except Exception as e:
                        logger.error(f"[KimiChat] 删除临时文件失败: {file_path}, 错误: {str(e)}")
                    
            # 清理 frames 目录
            if os.path.exists(self.frames_dir):
                for filename in os.listdir(self.frames_dir):
                    file_path = os.path.join(self.frames_dir, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logger.debug(f"[KimiChat] 已删除帧文件: {file_path}")
                    except Exception as e:
                        logger.error(f"[KimiChat] 删除帧文件失败: {file_path}, 错误: {str(e)}")
                    
        except Exception as e:
            logger.error(f"[KimiChat] 清理临时目录失败: {str(e)}")

    def extract_audio(self, video_path):
        """从视频中提取音频(使用moviepy)"""
        try:
            # 生成唯一的音频文件名
            audio_filename = f"audio_{int(time.time())}.mp3"
            audio_path = os.path.join(self.temp_dir, audio_filename)
            
            try:
                # 使用moviepy提取
                video = VideoFileClip(video_path)
                if video.audio:  # 确保视频有音
                    video.audio.write_audiofile(
                        audio_path,
                        codec='libmp3lame',
                        logger=None  # 禁用进度输出
                    )
                    video.close()  # 释放资源
                    
                    if os.path.exists(audio_path):
                        logger.info(f"[KimiChat] 音频提取成功: {audio_path}")
                        return audio_path
                    else:
                        logger.error("[KimiChat] 音频文件未生成")
                        return None
                else:
                    logger.warning("[KimiChat] 视频没有音轨")
                    return None
                
            except Exception as e:
                logger.error(f"[KimiChat] moviepy处理失败: {str(e)}")
                return None
            
        except Exception as e:
            logger.error(f"[KimiChat] 提取音频失败: {str(e)}")
            return None

    def transcribe_audio(self, audio_path, token):
        """转写音频为文本"""
        try:
            url = "https://api.siliconflow.cn/v1/audio/transcriptions"
            
            # 准备文件表数据
            files = {
                'file': ('audio.mp3', open(audio_path, 'rb'), 'audio/mpeg'),
                'model': (None, 'FunAudioLLM/SenseVoiceSmall')
            }
            
            headers = {
                "Authorization": f"Bearer {token}"
            }
            
            response = requests.post(url, files=files, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            return result.get('text', '')
        except Exception as e:
            logger.error(f"[KimiChat] 音频转写失败: {str(e)}")
            return None
        finally:
            # 确保文件已关闭
            for file in files.values():
                if hasattr(file[1], 'close'):
                    file[1].close()

    def handle_video_share(self, content, user_id, e_context, custom_prompt=None):
        """处理视频分享链接"""
        try:
            # 发送解析提示
            parse_reply = Reply(ReplyType.TEXT, "正在解析视频链接...")
            e_context["channel"].send(parse_reply, e_context["context"])
            
            # 提取标题和URL
            title, url = self.media_parser.extract_share_info(content)
            if not url:
                reply = Reply(ReplyType.TEXT, "未找到有效的视频链接")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 获取视频信息
            video_info = self.media_parser.get_video_info(url)
            if not video_info:
                logger.error(f"[KimiChat] 视频信息获取失败: {url}")
                reply = Reply(ReplyType.TEXT, "视频解析失败，请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

            # 获取无水印视频URL和标题信息
            video_url = video_info.get("play_url") or video_info.get("video_url")
            video_path = video_info.get("video_path")
            
            if not video_url and not video_path:
                logger.error(f"[KimiChat] 无法获取视频URL或路径")
                reply = Reply(ReplyType.TEXT, "获取视频失败，请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

            # 先发送无水印视频URL
            if video_url:
                logger.info(f"[KimiChat] 准备发送视频URL: {video_url}")
                url_reply = Reply()
                url_reply.type = ReplyType.VIDEO_URL
                url_reply.content = video_url
                e_context["channel"].send(url_reply, e_context["context"])

            # 获取或创建会话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }

            # 下载并处理视频
            if video_url and not video_path:
                video_path = self.download_video(video_url)
            
            if not video_path or not os.path.exists(video_path):
                reply = Reply(ReplyType.TEXT, "视频处理失败,请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

            # 并发处理视频帧提取和音频转文字
            with ThreadPoolExecutor(max_workers=2) as executor:
                frame_future = executor.submit(self.extract_frames, video_path)
                audio_future = executor.submit(self.process_audio, video_path)
            
            frames = frame_future.result()
            if not frames:
                reply = Reply(ReplyType.TEXT, "视频帧提取失败,请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            
            file_ids = self.upload_frames(frames)
            
            if not file_ids:
                reply = Reply(ReplyType.TEXT, "视频帧上传失败,请稍后重试") 
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 等待音频转文字结果,最多等待60秒
            try:
                audio_text = audio_future.result(timeout=60)
            except TimeoutError:
                logger.warning("[KimiChat] 音频转文字超时")
                audio_text = None
            
            # 构建提示词
            if custom_prompt:
                prompt = custom_prompt
            else:
                prompt = ""
                if video_info:
                    if video_info.get("title"):
                        prompt += f"【视频标题】{video_info['title']}\n"
                    if video_info.get("author"):
                        prompt += f"【作者】{video_info['author']}\n"
                elif title:
                    prompt += f"【视频标题】{title}\n"
                
                prompt += f"\n{self.video_summary_prompt}"
            
            if audio_text:
                prompt += f"\n\n音频内容：{audio_text}"
            
            # 获取分析结果
            rely_content = stream_chat_responses(
                chat_id=chat_id,
                content=prompt,
                refs=file_ids
            )
            
            if rely_content:
                formatted_content = "【视频分析】\n\n" + rely_content
                if custom_prompt and self.show_custom_prompt:
                    formatted_content = f"【提示词】{custom_prompt}\n\n" + formatted_content
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                final_reply = Reply(ReplyType.TEXT, formatted_content + tip_message)
                e_context["channel"].send(final_reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            else:
                reply = Reply(ReplyType.TEXT, "视频分析失败，请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

        except Exception as e:
            logger.error(f"[KimiChat] 处理视频分享失败: {e}")
            reply = Reply(ReplyType.TEXT, "处理视频分享失败，请稍后重试")
            e_context["channel"].send(reply, e_context["context"])
            e_context.action = EventAction.BREAK_PASS
            return True
        finally:
            # 清理临时文件
            try:
                if 'frames' in locals() and frames:
                    self.clean_temp_files([f[0] for f in frames])
            except Exception as e:
                logger.error(f"[KimiChat] 清理临时文件失败: {e}")

    def process_audio(self, video_path):
        """处理视频音频
        Args:
            video_path: 视频文件路径
        Returns:
            str: 音频转写文本
        """
        try:
            # 提取音频
            audio_path = self.extract_audio(video_path)
            if not audio_path:
                return None

            # 获取音频转写token
            audio_token = self.conf.get("audio_token")
            if not audio_token:
                logger.warning("[KimiChat] 未配置audio_token，跳过音频转写")
                return None

            # 转写音频
            audio_text = self.transcribe_audio(audio_path, audio_token)
            
            return audio_text

        except Exception as e:
            logger.error(f"[KimiChat] 处理音频失败: {e}")
            return None

    def clean_temp_files(self, file_paths):
        """清理临时文件
        Args:
            file_paths: 要清理的文件路径列表
        """
        try:
            for file_path in file_paths:
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.debug(f"[KimiChat] 已删除临时文件: {file_path}")
                    except Exception as e:
                        logger.error(f"[KimiChat] 删除临时文件失败: {file_path}, 错误: {str(e)}")
        except Exception as e:
            logger.error(f"[KimiChat] 清理临时文件失败: {str(e)}")

    def download_video(self, video_url):
        """下载视频到临时文件
        Args:
            video_url: 视频URL
        Returns:
            str: 下载后的视频文件路径，失败返回None
        """
        try:
            # 生成临时文件名
            video_filename = f"video_{int(time.time())}.mp4"
            video_path = os.path.join(self.temp_dir, video_filename)
            
            # 发送HTTP请求下载视频
            response = requests.get(video_url, stream=True)
            response.raise_for_status()
            
            # 获取文件大小
            file_size = int(response.headers.get('content-length', 0))
            
            # 检查文件大小限制
            max_size = self.conf.get("video_config", {}).get("max_size", 100) * 1024 * 1024  # 默认100MB
            if file_size > max_size:
                logger.error(f"[KimiChat] 视频文件过大: {file_size/1024/1024:.2f}MB > {max_size/1024/1024}MB")
                return None
            
            # 写入文
            with open(video_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            if os.path.exists(video_path):
                logger.info(f"[KimiChat] 视频下载成功: {video_path}")
                return video_path
            else:
                logger.error("[KimiChat] 视频文件未生成")
                return None
            
        except Exception as e:
            logger.error(f"[KimiChat] 下载视频失败: {str(e)}")
            # 如果文件已部下载，清理
            if 'video_path' in locals() and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                except:
                    pass
            return None

    def start_cleanup_task(self):
        """动定期清理任务"""
        def cleanup():
            while True:
                try:
                    time.sleep(3600)  # 每小时清理一次
                    self.clean_temp_directory()
                except Exception as e:
                    logger.error(f"[KimiChat] 清理任务出错: {str(e)}")
        
        cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        cleanup_thread.start()

    def process_video_file(self, video_path, user_id, e_context, custom_prompt=None):
        """处理视频文件"""
        try:
            # 获取或创建会话
            session_key = self.get_session_key(user_id, e_context['context'])
            if session_key in self.chat_sessions:
                chat_id = self.chat_sessions[session_key]['chat_id']
            else:
                chat_id = create_new_chat_session()
                self.chat_sessions[session_key] = {
                    'chat_id': chat_id,
                    'last_active': time.time(),
                    'use_search': True
                }

            # 处理视频帧
            manager = VideoFrameManager(output_dir=self.frames_dir)
            frames = manager.extract_frames(video_path, self.max_frames)
            if not frames:
                reply = Reply(ReplyType.TEXT, "视频帧提取失败,请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

            # 并发上传视频帧
            file_ids = self.upload_frames(frames)

            if not file_ids:  
                reply = Reply(ReplyType.TEXT, "视频帧上传失败,请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

            # 处理音频
            audio_text = self.process_audio(video_path)

            # 构建提示词
            if custom_prompt:
                prompt = custom_prompt
            else:
                prompt = self.video_summary_prompt
            
            if audio_text:
                prompt += f"\n\n音频内容：{audio_text}"

            # 获取分析结果
            rely_content = stream_chat_responses(
                chat_id=chat_id,
                content=prompt,
                refs=file_ids
            )

            if rely_content:
                formatted_content = "【视频分析】\n\n" + rely_content
                if custom_prompt and self.show_custom_prompt:
                    formatted_content = f"【提示词】{custom_prompt}\n\n" + formatted_content
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                final_reply = Reply(ReplyType.TEXT, formatted_content + tip_message)
                e_context["channel"].send(final_reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True
            else:
                reply = Reply(ReplyType.TEXT, "视频分析失败，请稍后重试")
                e_context["channel"].send(reply, e_context["context"])
                e_context.action = EventAction.BREAK_PASS
                return True

        except Exception as e:
            logger.error(f"[KimiChat] 处理视频文件失败: {e}")
            reply = Reply(ReplyType.TEXT, "处理视频失败，请稍后重试")
            e_context["channel"].send(reply, e_context["context"])
            e_context.action = EventAction.BREAK_PASS
            return True
        finally:
            # 清理临时文��
            try:
                if 'frames' in locals() and frames:
                    self.clean_temp_files([f[0] for f in frames])
            except Exception as e:
                logger.error(f"[KimiChat] 清理临时文件失败: {e}")

    def upload_frames(self, frames):
        """上传视频帧"""
        file_ids = []
        uploader = FileUploader()
        
        with ThreadPoolExecutor(max_workers=self.conf["video_config"]["upload_threads"]) as executor:
            futures = []
            for frame_path, _ in frames:
                future = executor.submit(uploader.upload,
                                         os.path.basename(frame_path), 
                                         frame_path,
                                         skip_notification=True)
                futures.append(future)
            
            for future in as_completed(futures):
                file_id = future.result()
                if file_id:
                    file_ids.append(file_id)
            
        return file_ids

    def extract_frames(self, video_path):
        """提取视频帧"""
        manager = VideoFrameManager(output_dir=self.frames_dir)
        return manager.extract_frames(video_path, self.max_frames)

