# coding=utf-8
"""
Author: chazzjimel
Email: chazzjimel@gmail.com
wechat：cheung-z-x

Description:

"""

import requests
import json
from typing import Optional, List, Union

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
    :return: 如果请求成功，返回会话ID(str)；如果失败，抛出异常。
    """
    # 从全局tokens变量中获取access_token
    auth_token = tokens['access_token']

    # 复制请求头并添加Authorization字段
    headers = HEADERS.copy()
    headers['Authorization'] = f'Bearer {auth_token}'

    # 定义请求的载��
    payload = {
        "name": "未命名会话",
        "is_example": False
    }

    # 发送POST请求
    response = requests.post('https://kimi.moonshot.cn/api/chat', json=payload, headers=headers)

    # 检查响应状态码并处理响应
    if response.status_code == 200:
        chat_id = response.json().get('id')
        if not chat_id:
            raise Exception("创建会话失败: 未获取到会话ID")
        logger.debug("[KimiChat] 新建会话ID操作成功！")
        return chat_id
    else:
        raise Exception(f"创建会话失败，状态码：{response.status_code}")


def get_headers():
    """获取请求头"""
    auth_token = tokens['access_token']
    headers = HEADERS.copy()
    headers['Authorization'] = f'Bearer {auth_token}'
    return headers


# 实现流式请求聊天数据的函数
@ensure_access_token
def stream_chat_responses(
    chat_id: str, 
    content: str,
    refs: Optional[Union[str, List[str]]] = None,
    **kwargs
) -> str:
    """发送消息并获取流式响应"""
    try:
        url = f"https://kimi.moonshot.cn/api/chat/{chat_id}/completion/stream"
        
        # 构造基本请求数据
        data = {
            "messages": [{
                "role": "user",
                "content": content
            }],
            "use_search": True,  # 默认开启联网
            "extend": {
                "sidebar": False,  # 开启侧边栏
            },
            "kimiplus_id": "kimi",  # 使用kimi模型
            "use_research": False,  # 不使用研究模式
            "use_math": False,  # 不使用数学模式
            "refs": [],  # 空引用列表
            "refs_file": []  # 空文件引用列表
        }
        
        # 处理文件引用
        if refs:
            data["refs"] = [refs] if isinstance(refs, str) else refs
            data["refs_file"] = []
            for ref_id in data["refs"]:
                file_info = get_file_info(ref_id)
                if file_info:
                    data["refs_file"].append(file_info)
        
        # 合并额外参数
        if kwargs:
            data.update(kwargs)
        
        # 发送请求
        response = requests.post(
            url,
            headers=get_headers(),
            json=data,
            stream=True,
            timeout=60  # 增加超时时间
        )
        
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

