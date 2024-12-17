from .media_parser import MediaParser
import logging
import re

logger = logging.getLogger(__name__)

def can_handle_url(text):
    """检查是否可以处理该链接"""
    # 支持的平台列表
    platforms = [
        "douyin.com", "iesdouyin.com",
        "kuaishou.com", "gifshow.com", "chenzhongtech.com",
        "bilibili.com", "b23.tv"
    ]
    
    # 检查是否包含支持的平台链接
    text = text.lower()
    for platform in platforms:
        if platform in text:
            logger.debug(f"[MediaParser] 检测到支持的平台: {platform}")
            return True
            
    logger.debug(f"[MediaParser] 未检测到支持的平台")
    return False

def extract_share_url(text):
    """从分享文本中提取URL
    Args:
        text: 分享文本
    Returns:
        str: 提取的URL
    """
    # 抖音分享文本格式
    if "复制打开抖音" in text:
        pattern = r'https://v\.douyin\.com/[a-zA-Z0-9]+/'
        match = re.search(pattern, text)
        if match:
            return match.group(0)
            
    # 快手分享文本格式
    elif "快手" in text:
        pattern = r'https://v\.kuaishou\.com/[a-zA-Z0-9]+'
        match = re.search(pattern, text)
        if match:
            return match.group(0)
            
    # B站分享文本格式
    elif "bilibili" in text or "b23.tv" in text:
        pattern = r'https?://(?:b23\.tv|www\.bilibili\.com)/[^\s]+'
        match = re.search(pattern, text)
        if match:
            return match.group(0)
            
    # 通用URL格式
    pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
    match = re.search(pattern, text)
    return match.group(0) if match else None

__all__ = ['MediaParser', 'can_handle_url', 'extract_share_url'] 