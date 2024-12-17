# coding=utf-8
"""
Author: chazzjimel
Email: chazzjimel@gmail.com
wechat：cheung-z-x

Description:

"""

import requests
from PIL import Image
import json
import os
import time
import uuid

from common.log import logger
from .token_manager import ensure_access_token, tokens


class FileUploader:
    def __init__(self):
        self.pre_sign_url_api = "https://kimi.moonshot.cn/api/pre-sign-url"
        self.file_upload_api = "https://kimi.moonshot.cn/api/file"
        self.parse_process_api = "https://kimi.moonshot.cn/api/file/parse_process"
        # 初始化session
        self.session = requests.Session()
        # 设置默认超时
        self.session.timeout = 30

    def get_object_name(self):
        """生成文件的对象名称"""
        user_id = "cl8o1j998ono9o6huco0"  # 从配置获取
        date = time.strftime("%Y-%m-%d")
        random_id = str(uuid.uuid4()).replace("-", "")[:20]
        return f"{user_id}/{date}/{random_id}"

    @ensure_access_token
    def get_presigned_url(self, file_name, is_image=False):
        """获取预签名URL"""
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "action": "image" if is_image else "file",
            "name": file_name
        }
        response = self.session.post(
            self.pre_sign_url_api, 
            headers=headers, 
            json=payload,
            timeout=self.session.timeout
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"获取预签名URL失败: {response.text}")

    def upload_file(self, url, file_path):
        """上传文件到预签名URL"""
        with open(file_path, 'rb') as file:
            response = requests.put(url, data=file)
            if response.status_code != 200:
                raise Exception(f"文件上传失败: {response.status_code}")

    def get_image_dimensions(self, file_path):
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                return str(width), str(height)
        except Exception as e:
            logger.error(f"获取图片尺寸失败: {e}")
            return "940", "940"

    @ensure_access_token
    def notify_file_upload(self, file_info, file_path=None, is_image=False):
        """通知服务器文件已上传"""
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }

        if is_image:
            width, height = self.get_image_dimensions(file_path)
            file_info.update({
                "type": "image",
                "file_id": file_info.get("file_id", ""),
                "meta": {
                    "width": width,
                    "height": height
                }
            })
        
        response = self.session.post(
            self.file_upload_api, 
            headers=headers, 
            json=file_info,
            timeout=self.session.timeout
        )
        if response.status_code == 200:
            return response.json().get("id")
        else:
            raise Exception(f"通知文件上传失败: {response.text}")

    @ensure_access_token
    def parse_process(self, ids, skip_notification=False):
        """通知服务器开始解析文件"""
        if skip_notification:
            return True
            
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "ids": [ids]
        }
        
        try:
            response = self.session.post(
                self.parse_process_api,
                headers=headers,
                json=payload,
                timeout=self.session.timeout
            )
            if response.status_code == 200:
                return True
            else:
                logger.error(f"通知解析文件失败: {response.text}")
                return False
        except Exception as e:
            logger.error(f"通知解析文件出错: {str(e)}")
            return False

    def upload(self, filename, filepath, skip_notification=False, timeout=30):
        """上传文件
        Args:
            filename: 文件名
            filepath: 文件路径
            skip_notification: 是否跳过通知
            timeout: 超时时间(秒)
        Returns:
            str: 文件ID
        """
        try:
            # 更新session超时时间
            self.session.timeout = timeout
            
            # 判断是否为图片
            is_image = filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'))
            
            # 1. 获取预签名URL
            pre_sign_info = self.get_presigned_url(filename, is_image)
            
            # 2. 上传文件到预签名URL
            with open(filepath, 'rb') as file:
                response = self.session.put(pre_sign_info['url'], data=file, timeout=timeout)
                if response.status_code != 200:
                    raise Exception(f"文件上传失败: {response.status_code}")
            
            # 3. 通知服务器文件已上传
            file_info = {
                "type": "image" if is_image else "file",
                "name": filename,
                "file_id": pre_sign_info.get('file_id', ''),
                "object_name": pre_sign_info['object_name']
            }
            
            if is_image:
                width, height = self.get_image_dimensions(filepath)
                file_info.update({
                    "meta": {
                        "width": width,
                        "height": height
                    }
                })
            
            file_id = self.notify_file_upload(file_info, filepath if is_image else None, is_image)
            
            # 4. 通知开始解析文件(根据skip_notification决定是否跳过)
            if not skip_notification:
                self.parse_process(file_id)
            
            return file_id
            
        except Exception as e:
            logger.error(f"上传过程中发生错误: {str(e)}")
            return None
