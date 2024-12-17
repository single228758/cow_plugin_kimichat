import re
import logging
import requests
import time
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlencode

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
        """从分享内容中提取标题和URL
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
            dict: 包含视频信息的字典,如果出错返回None
        """
        try:
            # 定义两个API的配置
            apis = [
                {
                    "url": "https://www.hhlqilongzhu.cn/api/sp_jx/sp.php",
                    "params": {"url": url},
                    "timeout": 3,  # 第一个API设置3秒超时
                    "parser": self._parse_api1_response
                },
                {
                    "url": "https://api.yujn.cn/api/dy_jx.php",
                    "params": {"msg": url},
                    "timeout": 10,  # 第二个API给更长的超时时间
                    "parser": self._parse_api2_response
                }
            ]

            # 创建任务列表
            futures = []
            with ThreadPoolExecutor(max_workers=2) as executor:
                for api in apis:
                    future = executor.submit(
                        self._make_request,
                        api["url"],
                        params=api["params"],
                        timeout=api["timeout"]
                    )
                    futures.append((future, api))

                # 等待第一个完成的任务
                for future, api in futures:
                    try:
                        response = future.result(timeout=api["timeout"])
                        if response and response.status_code == 200:
                            try:
                                # 使用对应的解析器处理响应
                                result = api["parser"](response)
                                if result:
                                    return result
                            except Exception as e:
                                logger.warning(f"[MediaParser] API解析失败: {e}")
                                continue
                    except Exception as e:
                        logger.warning(f"[MediaParser] API请求失败: {e}")
                        continue

            logger.error("[MediaParser] 所有API都失败")
            return None

        except Exception as e:
            logger.error(f"[MediaParser] 获取视频信息异常: {e}", exc_info=True)
            return None

    def _parse_api1_response(self, response):
        """解析第一个API的响应"""
        try:
            data = response.json()
            if data.get("code") == 200:
                video_data = data.get("data")
                if video_data and isinstance(video_data, dict):
                    video_url = video_data.get("url")
                    if video_url:
                        video_path = None
                        try:
                            video_path = self.download_video(video_url)
                        except Exception as e:
                            logger.warning(f"[MediaParser] 视频下载失败: {e}")

                        return {
                            "title": video_data.get("title", ""),
                            "author": video_data.get("author", ""),
                            "play_url": video_url,
                            "video_url": video_url,
                            "video_path": video_path,
                            "platform": self.get_platform(video_url)
                        }
        except Exception as e:
            logger.warning(f"[MediaParser] 解析第一个API响应失败: {e}")
        return None

    def _parse_api2_response(self, response):
        """解析第二个API的响应"""
        try:
            data = response.json()
            if "msg" in data and data["msg"] == "解析成功！💬️":
                video_url = data.get("video")
                if video_url:
                    video_path = None
                    try:
                        video_path = self.download_video(video_url)
                    except Exception as e:
                        logger.warning(f"[MediaParser] 视频下载失败: {e}")

                    return {
                        "title": data.get("title", ""),
                        "author": data.get("name", ""),
                        "play_url": video_url,
                        "video_url": video_url,
                        "video_path": video_path,
                        "platform": self.get_platform(video_url)
                    }
        except Exception as e:
            logger.warning(f"[MediaParser] 解析第二个API响应失败: {e}")
        return None

    def download_video(self, url):
        """下载视频
        Args:
            url: 视频URL
        Returns:
            str: 视频本地路径,下载失败返回None
        """
        try:
            # 生成唯一文件名
            filename = f"video_{int(time.time())}.mp4"
            filepath = os.path.join(self.temp_dir, filename)
            
            # 下载视频
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            # 检查响应头
            content_type = response.headers.get("Content-Type")
            if content_type and not content_type.startswith("video/"):
                logger.warning(f"[MediaParser] 下载的文件可能不是视频: {content_type}")
            
            # 写入文件
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        
            logger.info(f"[MediaParser] 视频下载成功: {filepath}")
            return filepath
            
        except requests.RequestException as e:
            logger.error(f"[MediaParser] 视频下载请求异常: {e}")
        except IOError as e:
            logger.error(f"[MediaParser] 视频写入文件异常: {e}")
        except Exception as e:
            logger.error(f"[MediaParser] 视频下载异常: {e}", exc_info=True)
        
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

    def _make_request(self, url, params=None, headers=None, max_retries=3, timeout=3):
        """发送HTTP请求
        Args:
            url: 请求URL
            params: 请求参数
            headers: 请求头
            max_retries: 最大重试次数
            timeout: 请求超时时间(秒)
        Returns:
            Response: 请求响应对象,如果请求失败返回None
        """
        headers = headers or {}
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        
        for i in range(max_retries):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout  # 使用传入的超时时间
                )
                response.raise_for_status()
                return response
            except (requests.RequestException, requests.Timeout) as e:
                logger.warning(f"[MediaParser] 请求失败({i+1}/{max_retries}): {e}")
                if i == max_retries - 1:
                    logger.error("[MediaParser] 请求多次失败,放弃重试")
                    return None
                time.sleep(0.5)  # 减少重试等待时间