import re
import logging
import requests
import time
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class MediaParser:
    def __init__(self, config=None):
        """初始化解析器
        Args:
            config: 配置信息字典
        """
        self.config = config or {}
        
        # 设置下载目录
        self.temp_dir = os.path.join('plugins', 'cow_plugin_kimichat', 'storage', 'temp')
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
        # 初始化线程池
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        # 支持的平台和对应的域名
        self.platforms = {
            "douyin": ["douyin.com", "iesdouyin.com"],
            "kuaishou": ["kuaishou.com", "gifshow.com"],
            "weibo": ["weibo.com", "weibo.cn"],
            "xiaohongshu": ["xiaohongshu.com", "xhslink.com"]
        }

    def extract_share_info(self, content):
        """从分享内容中提取标��和URL
        Args:
            content: 分享内容文本
        Returns:
            tuple: (标题, URL)
        """
        try:
            # 提取标题
            title = ""
            title_match = re.search(r'【(.+?)】', content)
            if title_match:
                title = title_match.group(1)
            
            # 提取URL
            url = None
            url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+(?:\?[^\s<>"]*)?(?:#[^\s<>"]*)?'
            url_match = re.search(url_pattern, content)
            if url_match:
                url = url_match.group(0)
            
            return title, url
            
        except Exception as e:
            logger.error(f"[MediaParser] 解析分享内容失败: {e}")
            return None, None

    def is_video_share(self, content):
        """判断是否是视频分享内容
        Args:
            content: 待检查的文本内容
        Returns:
            bool: 是否是视频分享
        """
        # 检查是否包含平台特征
        platform_patterns = [
            r'复制打开抖音',
            r'快手链接',
            r'微博视频',
            r'小红书视频'
        ]
        
        for pattern in platform_patterns:
            if re.search(pattern, content):
                return True
                
        # 检查是否包含支持的域名
        for platform in self.platforms.values():
            for domain in platform:
                if domain in content:
                    return True
                    
        return False

    def get_video_info(self, url):
        """获取视频信息
        Args:
            url: 视频链接
        Returns:
            dict: 包含视频信息的字典
        """
        try:
            # 使用API解析
            api_url = "https://www.hhlqilongzhu.cn/api/sp_jx/sp.php"
            params = {"url": url}
            
            response = self._make_request(api_url, params=params)
            if not response:
                return None
                
            data = response.json()
            if data.get("code") != 200:
                logger.error(f"[MediaParser] API返回错误: {data}")
                return None
            
            video_data = data.get("data", {})
            if not video_data:
                logger.error("[MediaParser] 未获取到视频信息") 
                return None
            
            # 获取无水印视频URL
            video_url = video_data["url"]
            
            # 异步下载视频,不阻塞返回
            video_path = None
            try:
                video_path = self.download_video(video_url)
            except Exception as e:
                logger.error(f"[MediaParser] 视频下载失败: {e}")
                # 下载失败不影响返回视频URL
                
            return {
                "title": video_data.get("title", ""),
                "author": video_data.get("author", ""),
                "play_url": video_url,  # 用于直接播放的URL
                "video_url": video_url,  # 兼容旧代码
                "video_path": video_path,
                "platform": self.get_platform(url)
            }
            
        except Exception as e:
            logger.error(f"[MediaParser] 获取视频信息失败: {e}")
            return None

    def download_video(self, url):
        """下载视频
        Args:
            url: 视频URL
        Returns:
            str: 视频本地路径
        """
        try:
            # 生成唯一文件名
            filename = f"video_{int(time.time())}.mp4"
            filepath = os.path.join(self.temp_dir, filename)
            
            # 下载视频
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        
            logger.info(f"[MediaParser] 视频下载成功: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"[MediaParser] 视频下载失败: {e}")
            return None

    def get_platform(self, url):
        """获取平台标识
        Args:
            url: 视频URL
        Returns:
            str: 平台标识
        """
        domain = urlparse(url).netloc
        
        for platform, domains in self.platforms.items():
            if any(d in domain for d in domains):
                return platform
                
        return "unknown"

    def _make_request(self, url, params=None, headers=None, max_retries=3):
        """发送HTTP请求
        Args:
            url: 请求URL
            params: 请求参数
            headers: 请求头
            max_retries: 最大重试次数
        Returns:
            Response: 请求响应对象
        """
        for i in range(max_retries):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=30
                )
                response.raise_for_status()
                return response
            except Exception as e:
                logger.warning(f"[MediaParser] 请求失败({i+1}/{max_retries}): {e}")
                if i == max_retries - 1:
                    return None
                time.sleep(1)