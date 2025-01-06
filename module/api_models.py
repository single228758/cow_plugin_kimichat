# coding=utf-8
"""
Author: chazzjimel
Email: chazzjimel@gmail.com
wechat：cheung-z-x

Description:

"""

import requests
import json

from common.log import logger
from .token_manager import ensure_access_token, tokens

# 常量定义，用于HTTP请求头
HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh-HK;q=0.9,zh;q=0.8',
    'Content-Type': 'application/json; charset=UTF-8',
    'Origin': 'https://kimi.moonshot.cn',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 '
                  'Safari/537.36'
}


# 创建新会话的函数
@ensure_access_token
def create_new_chat_session():
    """
    发送POST请求以创建新的聊天会话。
    :return: 如果请求成功，返回会话ID；如果失败，返回None。
    """
    # 从全局tokens变量中获取access_token
    auth_token = tokens['access_token']

    # 复制请求头并添加Authorization字段
    headers = HEADERS.copy()
    headers['Authorization'] = f'Bearer {auth_token}'

    # 定义请求的载荷
    payload = {
        "name": "未命名会话",
        "is_example": False
    }

    # 发送POST请求
    response = requests.post('https://kimi.moonshot.cn/api/chat', json=payload, headers=headers)

    # 检查响应状态码并处理响应
    if response.status_code == 200:
        logger.debug("[KimiChat] 新建会话ID操作成功！")
        return response.json().get('id')  # 返回会话ID
    else:
        logger.error(f"[KimiChat] 新建会话ID失败，状态码：{response.status_code}")
        return None


# 实现流式请求聊天数据的函数
@ensure_access_token
def stream_chat_responses(chat_id, content, refs=None, use_search=False, new_chat=False):
    """
    处理聊天响应
    :param chat_id: 会话ID
    :param content: 消息内容
    :param refs: 引用的文件ID列表
    :param use_search: 是否使用搜索
    :param new_chat: 是否新会话
    :return: 响应内容
    """
    auth_token = tokens['access_token']
    headers = {
        'Authorization': f'Bearer {auth_token}',
        'Content-Type': 'application/json',
        'Origin': 'https://kimi.moonshot.cn',
        'Referer': f'https://kimi.moonshot.cn/chat/{chat_id}'
    }
    
    # 构建请求数据
    data = {
        "messages": [{
            "role": "user",
            "content": content
        }],
        "use_search": use_search,
        "extend": {"sidebar": True},
        "kimiplus_id": "kimi",
        "use_research": False,
        "use_math": False
    }
    
    # 处理文件引用
    if refs:
        # 确保refs是列表
        if isinstance(refs, str):
            refs = [refs]
        
        data["refs"] = refs
        refs_file = []
        
        for ref_id in refs:
            try:
                file_info = get_file_info(ref_id)
                if file_info:
                    is_image = file_info.get('type') == 'image'
                    ref_data = {
                        "id": ref_id,
                        "name": file_info.get("name", ""),
                        "size": file_info.get("size", 0),
                        "file": {},
                        "upload_progress": 100,
                        "upload_status": "success",
                        "parse_status": "success",
                        "detail": {
                            "id": ref_id,
                            "name": file_info.get("name", ""),
                            "parent_path": "",
                            "type": file_info.get("type", "file"),
                            "size": file_info.get("size", 0),
                            "status": "parsed",
                            "presigned_url": file_info.get("presigned_url", ""),
                            "text_presigned_url": file_info.get("text_presigned_url", ""),
                            "content_type": file_info.get("content_type", ""),
                            "uploaded_at": file_info.get("uploaded_at", ""),
                            "created_at": file_info.get("created_at", ""),
                            "updated_at": file_info.get("updated_at", "")
                        },
                        "file_info": {
                            "id": ref_id,
                            "name": file_info.get("name", ""),
                            "highlight_name": "",
                            "type": file_info.get("type", "file"),
                            "content_type": file_info.get("content_type", ""),
                            "status": "parsed",
                            "size": file_info.get("size", 0),
                            "token_size": file_info.get("token_size", 0),
                            "failed_reason": ""
                        },
                        "done": True
                    }
                    
                    # 如果是图片，添加额外的URL信息
                    if is_image:
                        ref_data["detail"].update({
                            "preview_url": file_info.get("preview_url", ""),
                            "thumbnail_url": file_info.get("thumbnail_url", ""),
                            "mini_url": file_info.get("mini_url", ""),
                            "extra_info": {
                                "width": file_info.get("extra_info", {}).get("width", 0),
                                "height": file_info.get("extra_info", {}).get("height", 0)
                            }
                        })
                    
                    refs_file.append(ref_data)
            except Exception as e:
                logger.error(f"[KimiChat] 获取文件信息失败: {str(e)}")
                continue
        
        if refs_file:
            data["refs_file"] = refs_file
    
    try:
        # 发送预处理请求
        pre_url = f"https://kimi.moonshot.cn/api/chat/{chat_id}/pre-n2s"
        pre_response = requests.post(pre_url, headers=headers, json=data)
        pre_response.raise_for_status()
        
        # 发送实际的聊天请求
        url = f"https://kimi.moonshot.cn/api/chat/{chat_id}/completion/stream"
        response = requests.post(url, headers=headers, json=data, stream=True)
        response.raise_for_status()
        
        content = ""
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    try:
                        json_data = json.loads(line[6:])
                        if json_data.get('event') == 'cmpl' and 'text' in json_data:
                            content += json_data['text']
                    except json.JSONDecodeError:
                        continue
        
        final_content = content.strip()
        if not final_content:
            logger.error("[KimiChat] 未获取到有效回复内容")
            return "很抱歉，处理失败，请重试。"
            
        return final_content
        
    except Exception as e:
        logger.error(f"[KimiChat] 发送消息失败: {str(e)}")
        return f"处理失败: {str(e)}"

def get_file_info(file_id):
    """获取文件信息"""
    try:
        # 构建基本的文件信息
        file_info = {
            "id": file_id,
            "name": f"{file_id}.jpg",  # 默认文件名
            "size": 0,
            "file": {},
            "upload_progress": 100,
            "upload_status": "success",
            "parse_status": "success",
            "detail": {
                "id": file_id,
                "name": f"{file_id}.jpg",
                "parent_path": "",
                "type": "image",
                "size": 0,
                "status": "parsed",
                "content_type": "image/jpeg",
                "extra_info": {
                    "width": "940",
                    "height": "940"
                }
            },
            "file_info": file_id,  # 只需要提供文件ID
            "done": True
        }
        
        logger.debug(f"[KimiChat] 构建文件信息: {file_info}")
        return file_info
        
    except Exception as e:
        logger.error(f"[KimiChat] 获取文件信息失败: {str(e)}")
        return None

