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

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from plugins import *
from .module.token_manager import tokens, refresh_access_token
from .module.api_models import create_new_chat_session, stream_chat_responses
from .module.file_uploader import FileUploader


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
            # 确保 tmp 目录存在
            if not os.path.exists('tmp'):
                os.makedirs('tmp')
                logger.info("[KimiChat] 创建 tmp 目录")
            
            # 设置日志编码
            import sys
            if sys.stdout.encoding != 'utf-8':
                import codecs
                sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
                sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
            
            # 加载配置
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "config.json")
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                content = ''.join(char for char in content if ord(char) >= 32 or char in '\n\r\t')
                self.conf = json.loads(content)

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
            
            # 基础配置
            self.keyword = self.conf["keyword"]
            self.reset_keyword = self.conf["reset_keyword"] 
            self.toggle_search_keyword = self.conf["toggle_search_keyword"]
            
            # 群组配置
            self.group_names = self.conf["group_names"]
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
            
        except Exception as e:
            logger.error(f"[KimiChat] 初始化失败: {str(e)}", exc_info=True)
            raise e

        # 添加会话管理相关属性
        self.chat_sessions = {}  # 格式: {session_key: {'chat_id': chat_id, 'last_active': timestamp}}

    def check_file_format(self, file_path):
        """检查文件格式是否支持"""
        if not file_path:
            return False
        
        # 获取文件扩展名
        ext = os.path.splitext(file_path)[1].lower()
        
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
        
        # 添加日志输出便于调试
        if not is_supported:
            logger.warning(f"[KimiChat] 文件格式检查: 扩展名={ext}, 是否支持={is_supported}")
            logger.debug(f"[KimiChat] 支持的格式列表: {supported_formats}")
        
        return is_supported

    def get_valid_file_path(self, content):
        """获取有效的文件路径"""
        # 检查文件路径
        file_paths = [
            content,  # 原始路径
            os.path.abspath(content),  # 绝对路径
            os.path.join('tmp', os.path.basename(content)),  # tmp目录
            os.path.join(os.getcwd(), 'tmp', os.path.basename(content)),  # 完整tmp目录
        ]
        
        for path in file_paths:
            logger.debug(f"[KimiChat] 尝试路径: {path}")
            if os.path.isfile(path):
                logger.debug(f"[KimiChat] 找到文件: {path}")
                return path
        
        return None

    def extract_url(self, content):
        """从内容中提取URL并格式化为Kimi所需的格式"""
        if not content:
            return None
        
        # 更精确的URL匹配模式
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+(?:\?[^\s<>"]*)?(?:#[^\s<>"]*)?'
        urls = re.findall(url_pattern, content)
        if urls:
            url = urls[0]
            # 检查是否是需要排除的链接
            for exclude_url in self.exclude_urls:
                if exclude_url in url:
                    logger.info(f"[KimiChat] 检测到排除链接，跳过处理: {url}")
                    return None
            
            # 处理HTML实体编码
            url = url.replace('&amp;', '&')
            
            # 添加调试日志
            logger.debug(f"[KimiChat] 提取到URL: {url}")
            
            return f'<url id="" type="url" status="" title="" wc="">{url}</url>'
        return None

    def handle_url_content(self, content, user_id, e_context):
        """统一处理URL内容的函数"""
        # 从内容中提取URL和自定义提示词
        content = content.strip()
        custom_prompt = None
        
        # 检查是否有自定义提示词
        if content.startswith(self.keyword):
            content = content[len(self.keyword):].strip()
        
        # 分离提示词和URL
        parts = content.split('http', 1)
        if len(parts) == 2:
            custom_prompt = parts[0].strip()
            url = 'http' + parts[1].strip()
        else:
            url = content
        
        formatted_url = self.extract_url(url)
        if formatted_url:
            # 使用自定义提示词或默认提示词
            actual_prompt = custom_prompt if custom_prompt else self.summary_prompt
            actual_content = f"{actual_prompt}\n\n{formatted_url}"
            logger.info(f"[KimiChat] 检测到URL,使用提示词: {actual_prompt}")
            logger.info(f"[KimiChat] 格式化内容: {actual_content}")
            
            # 使用现有会话或创建新会话
            if user_id in self.chat_data:
                chat_info = self.chat_data[user_id]
                chat_id = chat_info['chatid']
                rely_content = stream_chat_responses(chat_id, actual_content, use_search=True)
            else:
                chat_id = create_new_chat_session()
                rely_content = stream_chat_responses(chat_id, actual_content, new_chat=True)
                self.chat_data[user_id] = {'chatid': chat_id, 'use_search': True}
            
            rely_content = self.clean_references(rely_content)
            tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
            reply = Reply(ReplyType.TEXT, rely_content + tip_message)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        return False

    def on_handle_context(self, e_context: EventContext):
        """处理消息上下文"""
        if not e_context['context'].content:
            return

        content = e_context['context'].content.strip()
        context_type = e_context['context'].type
        
        # 获取用户信息
        msg = e_context['context'].kwargs.get('msg')
        user_id = msg.from_user_id if msg else None
        isgroup = e_context['context'].kwargs.get('isgroup', False)
        
        # 修改群组检查逻辑
        if isgroup:
            group_name = msg.other_user_nickname if msg else None
            
            # 检查是否在允许的群组列表中
            allowed_groups = self.conf.get("allowed_groups", [])
            if allowed_groups and group_name not in allowed_groups:
                logger.debug(f"[KimiChat] 群组不在允许列表中: {group_name}")
                return
            
            # 对于链接自动总结功能，单独检查 group_names
            if context_type == ContextType.SHARING and self.auto_summary:
                if group_name not in self.conf.get("group_names", []):
                    logger.debug(f"[KimiChat] 群组不在自动总结表中: {group_name}")
                    return
        
        # 处理重置会话命令
        if content == self.reset_keyword:
            success, message = self.reset_chat(user_id, e_context['context'])
            if success:
                reply = Reply(ReplyType.TEXT, f"{message}\n\n发送 k+问题 可以继续追问")
            else:
                reply = Reply(ReplyType.TEXT, message)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        
        # 处理分享类型消息
        if context_type == ContextType.SHARING and self.auto_summary:
            # 判断是否是群聊
            if isgroup:
                # 检查群组名单
                group_name = msg.other_user_nickname if msg else None
                if group_name not in self.conf.get("group_names", []):
                    logger.debug(f"[KimiChat] 群组不在自动总结表中: {group_name}")
                    return
                logger.info(f"[KimiChat] 收到群聊分享链接: {content}")
                return self.handle_url_content(content, user_id, e_context)
            else:
                # 私聊消息，检查私聊自动总结开关
                if not self.conf.get("private_auto_summary", False):
                    logger.debug("[KimiChat] 私聊自动总结功能已关闭")
                    return
                logger.info(f"[KimiChat] 收到私聊分享链接: {content}")
                return self.handle_url_content(content, user_id, e_context)
        
        # 处理文本消息
        if context_type == ContextType.TEXT:
            content = content.strip()
            
            # 检查是否是文件识别触发词
            for trigger in self.file_triggers:
                if content.startswith(trigger):
                    logger.info(f"[KimiChat] 用户 {user_id} 触发文件识别")
                    return self.handle_file_trigger(trigger, content, user_id, e_context)
            
            # 处理普通文本对话
            if self.keyword == "" or content.startswith(self.keyword):
                # 检查是否包含URL
                if 'http' in content:
                    return self.handle_url_content(content, user_id, e_context)
                
                # 移除关键词前缀
                if self.keyword and content.startswith(self.keyword):
                    content = content[len(self.keyword):].strip()
                
                # 处理普通对话
                if user_id in self.chat_data:
                    chat_info = self.chat_data[user_id]
                    chat_id = chat_info['chatid']
                    rely_content = stream_chat_responses(chat_id, content, use_search=True)
                else:
                    chat_id = create_new_chat_session()
                    rely_content = stream_chat_responses(chat_id, content, new_chat=True)
                    self.chat_data[user_id] = {'chatid': chat_id, 'use_search': True}
                
                rely_content = self.clean_references(rely_content)
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
        
        # 处理文件上传
        if context_type in [ContextType.FILE, ContextType.IMAGE]:
            # 获取真实的用户ID
            real_user_id = msg.actual_user_id if msg else user_id
            
            # 构造唯一的等待ID
            is_group = e_context["context"].kwargs.get('isgroup', False)
            group_id = e_context["context"].kwargs['msg'].other_user_id if is_group else None
            waiting_id = f"{group_id}_{real_user_id}" if is_group else real_user_id
            
            # 添加调试日志
            logger.debug(f"[KimiChat] 收到文件，waiting_id={waiting_id}, user_id={real_user_id}, group_id={group_id}")
            logger.debug(f"[KimiChat] 当前等待列表: {list(self.waiting_files.keys())}")
            
            # 先检查是否有等待记录
            if waiting_id not in self.waiting_files:
                logger.debug(f"[KimiChat] 未找到等待记录，忽略文件")
                return False
            
            waiting_info = self.waiting_files[waiting_id]
            logger.debug(f"[KimiChat] 找到等待记录: {waiting_info}")
            logger.debug(f"[KimiChat] 验证信息: trigger_user={waiting_info.get('trigger_user_id')}, current_user={real_user_id}, is_group={is_group}, group_id={group_id}")
            
            # 添加超时检查
            if time.time() - waiting_info.get('trigger_time', 0) > waiting_info.get('timeout', 300):
                # 超时清理
                self.clean_waiting_files(waiting_id)
                reply = Reply(ReplyType.TEXT, "等待文件超时，请重新发送触发指令")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 验证发送者身份
            if waiting_info.get('trigger_user_id') != real_user_id:
                logger.debug(f"[KimiChat] 用户身份不匹配: trigger={waiting_info.get('trigger_user_id')}, current={real_user_id}")
                # 不是触发用户发送的文件，静默忽略
                return False
            
            # 验证群组上下文
            if is_group != waiting_info.get('is_group') or (is_group and group_id != waiting_info.get('group_id')):
                logger.debug(f"[KimiChat] 群组上下文不匹配: is_group={is_group}, group_id={group_id}")
                # 上下文不匹配，静默忽略
                return False
            
            try:
                # 开始处理文件
                logger.info(f"[KimiChat] 开始处理文件: {content}")
                
                if hasattr(msg, 'prepare'):
                    msg.prepare()
                    time.sleep(1)  # 添加延迟等待文件准备
                
                file_path = self.get_valid_file_path(content)
                logger.info(f"[KimiChat] 获取到文件路径: {file_path}")
                
                if not file_path:
                    logger.warning("[KimiChat] 文件路径无效")
                    reply = Reply(ReplyType.TEXT, "文件不存在")
                    e_context['reply'] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                
                # 检查文件格式
                if not self.check_file_format(file_path):
                    logger.warning(f"[KimiChat] 不支持的文件格式: {file_path}")
                    reply = Reply(ReplyType.TEXT, "不支持的文件格式")
                    e_context['reply'] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                
                # 上传文件
                current_filename = os.path.basename(file_path)
                logger.info(f"[KimiChat] 开始上传文件: {current_filename}")
                uploader = FileUploader()
                file_id = uploader.upload(current_filename, file_path)
                
                if not file_id:
                    logger.error("[KimiChat] 文件上传失败")
                    raise Exception("文件上传失败")
                
                logger.info(f"[KimiChat] 文件上传成功: id={file_id}")
                
                # 记录已处理的文件信息
                waiting_info['received'].append(file_id)
                waiting_info['received_files'].append({
                    'id': file_id,
                    'name': current_filename
                })
                
                # 检查是否已收集足够的文件
                if len(waiting_info['received']) >= waiting_info['count']:
                    # 发送处理提示
                    processing_reply = Reply(ReplyType.TEXT, "文件接收完毕，正在解析处理中，请稍候...")
                    e_context["channel"].send(processing_reply, e_context["context"])
                    
                    # 开始处理文件
                    refs_list = waiting_info['received']
                    custom_prompt = waiting_info['prompt']
                    
                    # 根据文件类型选择提示词
                    if context_type == ContextType.IMAGE:
                        if not custom_prompt:
                            custom_prompt = self.image_prompts
                        logger.info(f"[KimiChat] 使用图片提示词: {custom_prompt}")
                    else:
                        if not custom_prompt:
                            custom_prompt = self.file_parsing_prompts
                        logger.info(f"[KimiChat] 使用文件提示词: {custom_prompt}")
                    
                    # 创建新会话并处理文件
                    chat_id = create_new_chat_session()
                    rely_content = stream_chat_responses(chat_id, custom_prompt, refs_list, False, True)
                    self.chat_data[user_id] = {'chatid': chat_id, 'use_search': False}
                    
                    if rely_content:
                        # 添加提示信
                        tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                        reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                    else:
                        reply = Reply(ReplyType.TEXT, "处理失败，请重试")
                    
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    
                    # 清理状态
                    self.clean_waiting_files(waiting_id)
                    return True
                else:
                    # 还需要更多文件
                    remaining = waiting_info['count'] - len(waiting_info['received'])
                    reply = Reply(ReplyType.TEXT, f"已接收{len(waiting_info['received'])}个文件，还需要{remaining}个")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return True
                
            except Exception as e:
                logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
                self.clean_waiting_files(waiting_id)
                reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            else:
                # 如果没有找到等待记录，直接返回
                logger.debug(f"[KimiChat] 未找到等待记录，忽略文件")
                return False
        
        return False

    def clean_references(self, text):
        """清理引用标记"""
        if not text:
            return text
        # 移除引用标记
        text = re.sub(r'\[\^\d+\^\]', '', text)
        # 移参考文献部分
        text = re.sub(r'参考文献：[\s\S]*$', '', text)
        return text.strip()

    def handle_files(self, user_id, prompt):
        """处理上传的文件"""
        try:
            # 添加会话参数验证
            if not prompt:
                prompt = self.file_parsing_prompts
            
            # 创建新会话时添加重试机制
            chat_id = None
            max_retries = 3
            for i in range(max_retries):
                try:
                    chat_id = create_new_chat_session()
                    break
                except Exception as e:
                    if i == max_retries - 1:
                        raise
                    logger.warning(f"[KimiChat] 创建会话失败,正在重试: {str(e)}")
                    time.sleep(1)
                
            # 其余逻辑保持不变
            ...

        except Exception as e:
            logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
            return Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")

    def process_file(self, file_path, user_id, e_context):
        """处理上传的文件"""
        try:
            # 检查文件格式
            if not self.check_file_format(file_path):
                reply = Reply(ReplyType.TEXT, "不支持的文件格式")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return False
            
            user_info = self.waiting_files.get(user_id)
            if not user_info:
                return False
            
            # 添加上传进度提示
            current_count = len(user_info['received']) + 1
            total_count = user_info['count']
            progress_msg = f"正在上传第 {current_count}/{total_count} 个文��..."
            e_context["channel"].send(Reply(ReplyType.TEXT, progress_msg), e_context["context"])
            
            # 异步上传文
            def upload_file():
                try:
                    uploader = FileUploader()
                    file_id = uploader.upload(os.path.basename(file_path), file_path)
                    if file_id:
                        user_info['received'].append(file_id)
                        user_info['received_files'].append(file_path)
                        logger.info(f"[KimiChat] 文件上传成功: {file_id}")
                        return file_id
                    return None
                except Exception as e:
                    logger.error(f"[KimiChat] 文件上传失败: {str(e)}")
                    return None

            # 启动异步上传
            import threading
            upload_thread = threading.Thread(target=upload_file)
            upload_thread.start()
            upload_thread.join(timeout=30)  # 设置超时时间
            
            # 检查上传结果
            if len(user_info['received']) >= user_info['count']:
                # 所有文件上传完成,开始处理
                processing_reply = Reply(ReplyType.TEXT, "所有文件上传完成,正在处理中...")
                e_context["channel"].send(processing_reply, e_context["context"])
                
                # 异步处理文件
                def process_files():
                    try:
                        refs_list = user_info['received']
                        custom_prompt = user_info['prompt']
                        
                        # 创建会话并处理
                        chat_id = create_new_chat_session()
                        rely_content = stream_chat_responses(chat_id, custom_prompt, refs_list, False, True)
                        
                        if rely_content:
                            tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                            reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                        else:
                            reply = Reply(ReplyType.TEXT, "处理失败,请重试")
                            
                        e_context["channel"].send(reply, e_context["context"])
                        
                    finally:
                        self.clean_waiting_files(user_id)
                        
                # 启动异步处理
                process_thread = threading.Thread(target=process_files)
                process_thread.start()
                
                return True
                
            else:
                # 还有文件等待上传
                remaining = user_info['count'] - len(user_info['received'])
                reply = Reply(ReplyType.TEXT, f"已接收 {len(user_info['received'])} 个文件,还需要 {remaining} 个")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
                
        except Exception as e:
            logger.error(f"[KimiChat] 处理文件出错: {str(e)}")
            self.clean_waiting_files(user_id)
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return False

    def handle_normal_chat(self, content, user_id, e_context):
        """处理普通对话"""
        msg = content[len(self.keyword):].strip()
        logger.info(f"[KimiChat] 收到消息: {msg}")
        
        try:
            # 创建新会话
            chat_id = create_new_chat_session()
            
            # 发送消息并获取回复
            rely_content = stream_chat_responses(chat_id, msg)
            rely_content = self.clean_references(rely_content)
            
            if rely_content:
                # 添加提示信息
                tip_message = f"\n\n发送 {self.keyword}+问题 可以继续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
                
        except Exception as e:
            logger.error(f"[KimiChat] 处理消息出错: {str(e)}")
            
        return False

    def process_files(self, user_id, e_context):
        """处理已接收的文件"""
        try:
            files = self.waiting_files.get(user_id, [])
            if not files or len(files) < 2:  # 至少需要有元数据和一个文件
                logger.error(f"[KimiChat] 用户 {user_id} 没有待处理的文件")
                return False
            
            # 获取自定义提示词和文件列表
            metadata = files[0]  # 第一个元素是元数据
            custom_prompt = metadata.get("custom_prompt")
            file_list = files[1:]  # 其余元素是文件信息
            
            # 创建新会话
            chat_id = create_new_chat_session()
            
            # 传文件并获取回复
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
                    
                    # 如果有定义提示词，使用自定义提示词
                    if custom_prompt:
                        prompt = custom_prompt
                    
                    logger.info(f"[KimiChat] 上传文件 {file_path} 使用提示词: {prompt}")
                    
                    # 上传文件
                    file_uploader = FileUploader()
                    file_id = file_uploader.upload(os.path.basename(file_path), file_path)
                    
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
                    
            # 清理临时文件和记录
            self.clean_waiting_files(user_id)
            return True
            
        except Exception as e:
            logger.error(f"[KimiChat] 处理文件上传出错: {str(e)}")
            self.clean_waiting_files(user_id)
            return False

    def handle_file_recognition(self, file_path, user_id, e_context, custom_prompt=None):
        """处理文件识别"""
        try:
            logger.info(f"[KimiChat] 开始处理文: {file_path}")
            
            # 获取文件类型
            file_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            
            # 根据文件类型择提示词
            if file_type.startswith("image"):
                prompt = custom_prompt or self.image_prompts
            else:
                prompt = custom_prompt or self.file_parsing_prompts
            
            logger.info(f"[KimiChat] 使用提示词: {prompt}")
            
            # 创建新会话
            chat_id = create_new_chat_session()
            
            # 上传文件
            file_uploader = FileUploader()
            file_id = file_uploader.upload(os.path.basename(file_path), file_path)
            
            if not file_id:
                logger.error(f"[KimiChat] 文件上传失败")
                reply = Reply(ReplyType.TEXT, "文件上传失败，请重试")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 发送提示词和文件ID
            rely_content = stream_chat_responses(chat_id, prompt, file_id)
            
            # 清理引用标
            rely_content = self.clean_references(rely_content)
            
            if rely_content:
                # 添加提示信息
                tip_message = f"\n\n发送 {self.keyword}+问题 可以续追问"
                reply = Reply(ReplyType.TEXT, rely_content + tip_message)
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
                
        except Exception as e:
            logger.error(f"[KimiChat] 处理文件识别出错: {str(e)}")
            reply = Reply(ReplyType.TEXT, f"处理文件时出错: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        
        return False

    def process_waiting_files(self, user_id, e_context):
        """处理等待中的文件"""
        try:
            if user_id not in self.waiting_files:
                return False
            
            waiting_info = self.waiting_files[user_id]
            
            # 检查处理是否超时
            if time.time() - waiting_info['trigger_time'] > waiting_info['timeout']:
                logger.warning(f"[KimiChat] 文件处理超时: {user_id}")
                self.clean_waiting_files(user_id)
                reply = Reply(ReplyType.TEXT, "文件处理超时,请重新上传")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            
            # 其余处理逻辑保持不变
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
                # 理临时文件
                received_files = waiting_info.get('received_files', [])
                for file_info in received_files:
                    if isinstance(file_info, dict) and 'path' in file_info:
                        file_path = file_info['path']
                        try:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                                logger.debug(f"[KimiChat] 删除临时文件: {file_path}")
                        except Exception as e:
                            logger.error(f"[KimiChat] 删除临时文件失败: {str(e)}")
                            continue
                
                # 删除等待状态
                del self.waiting_files[user_id]
                logger.debug(f"[KimiChat] 清理用户 {user_id} 的文件数据")
        except Exception as e:
            logger.error(f"[KimiChat] 清理文件出错: {str(e)}")
            # 确保即使出错也删除等待状态
            if user_id in self.waiting_files:
                del self.waiting_files[user_id]

    def handle_file_trigger(self, trigger, content, user_id, e_context):
        """处理文件识触发"""
        # 获取真实的用户ID
        msg = e_context['context'].kwargs.get('msg')
        real_user_id = msg.actual_user_id if msg else user_id
        
        logger.info(f"[KimiChat] 用户 {real_user_id} 触发文件识别, 内容: {content}")
        
        # 构造唯一的等待ID
        is_group = e_context["context"].kwargs.get('isgroup', False)
        group_id = e_context["context"].kwargs['msg'].other_user_id if is_group else None
        waiting_id = f"{group_id}_{real_user_id}" if is_group else real_user_id
        
        logger.debug(f"[KimiChat] 创建等待ID: {waiting_id}, group_id={group_id}, user_id={real_user_id}")
        
        # 检查是否有未完成的处理
        if waiting_id in self.waiting_files:
            waiting_info = self.waiting_files[waiting_id]
            if time.time() - waiting_info.get('trigger_time', 0) <= waiting_info.get('timeout', 300):
                reply = Reply(ReplyType.TEXT, "您有未完成的文件处理，请先完成或等待超时")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return True
            else:
                # 已超时，清理旧状态
                self.clean_waiting_files(waiting_id)
        
        # 解析文件数量和自定义提示词
        remaining = content[len(trigger):].strip()
        file_count = 1
        custom_prompt = None
        
        # 检查是否指定了文件数量
        match = re.match(r'(\d+)\s*(.*)', remaining)
        if match:
            file_count = int(match.group(1))
            custom_prompt = match.group(2).strip() if match.group(2) else None
        else:
            custom_prompt = remaining if remaining else None
        
        if file_count > 50:  # Kimi的最大限制
            reply = Reply(ReplyType.TEXT, "最多支持同时上传50个文件")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return True
        
        # 使用新的waiting_id存储状态
        self.waiting_files[waiting_id] = {
            'count': file_count,
            'received': [],
            'received_files': [],
            'prompt': custom_prompt,
            'trigger_time': time.time(),
            'timeout': 120,  # 5分钟超时
            'trigger_user_id': real_user_id,  # 使用真实用户ID
            'is_group': is_group,
            'group_id': group_id
        }
        logger.debug(f"[KimiChat] 已创建等待记录: {self.waiting_files[waiting_id]}")
        
        # 返回更详的等待提示
        timeout_minutes = 5
        reply_text = (
            f"请在{timeout_minutes}分钟内发送{file_count}个文件或图片\n"
            #f"超时后需要重新发送触发指令"
        )
        
        reply = Reply(ReplyType.TEXT, reply_text)
        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS
        return True

    def get_session_key(self, user_id, context):
        """生成话键值，区分群聊和私聊
        群聊: 整个群共享一个会话
        私聊: 每个用户独立会话
        """
        if context.kwargs.get('isgroup', False):
            group_id = context.kwargs['msg'].other_user_id  # 群ID
            return f"group_{group_id}"  # 群聊只使用群ID作为key
        return f"private_{user_id}"  # 私聊使用用户ID

    def get_or_create_session(self, user_id, context):
        """获取或创建用户会话"""
        session_key = self.get_session_key(user_id, context)
        
        if session_key not in self.chat_sessions:
            # 创建新会话
            chat_id = create_new_chat_session()
            self.chat_sessions[session_key] = {
                'chat_id': chat_id,
                'last_active': time.time(),
                'use_search': True
            }
            logger.info(f"[KimiChat] 创建新会话: key={session_key}, chat_id={chat_id}")
        
        return self.chat_sessions[session_key]

    def reset_chat(self, user_id, context):
        """重置用户会话"""
        try:
            session_key = self.get_session_key(user_id, context)
            
            if context.kwargs.get('isgroup', False):
                reply_text = "已重置本群的对话，所有群成员将开始新的对话。"
            else:
                reply_text = "已重置与您的私聊对话。"
            
            # 清理会话数据
            if session_key in self.chat_sessions:
                del self.chat_sessions[session_key]
            
            # 清理等待文件数据
            self.clean_waiting_files(user_id)
            
            logger.info(f"[KimiChat] 已重置会话: {session_key}")
            return True, reply_text
        except Exception as e:
            logger.error(f"[KimiChat] 重置会话出错: {str(e)}")
            return False, "重置会话时出现错误，请稍后重试"

    def handle_message(self, context):
        group_name = context.get("group_name")
        if group_name not in self.conf.get("allowed_groups", []):
            return  # 如果不在允的群组列表中，直接返回
        
        # 继续理消息的其他逻辑
        ...
