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

    def get_object_name(self):
        """生成文件的对象名称"""
        # 从你的 curl 示例可以看出格式是: user_id/date/random_id
        user_id = "cl8o1j998ono9o6huco0"  # 这个应该从配置或登录信息中获取
        date = time.strftime("%Y-%m-%d")
        random_id = str(uuid.uuid4()).replace("-", "")[:20]  # 生成一个随机ID
        return f"{user_id}/{date}/{random_id}"

    @ensure_access_token
    def get_presigned_url(self, file_name, is_image=False):
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "action": "image" if is_image else "file",
            "name": file_name
        }
        logger.debug(f"[KimiChat] 获取预签名URL请求头: {headers}")
        logger.debug(f"[KimiChat] 获取预签名URL请求体: {payload}")
        response = requests.post(self.pre_sign_url_api, headers=headers, json=payload)
        logger.debug(f"[KimiChat] 获取预签名URL响应状态码: {response.status_code}")
        logger.debug(f"[KimiChat] 获取预签名URL响应内容: {response.text}")
        
        if response.status_code == 200:
            response_data = response.json()
            logger.debug(f"[KimiChat] 获取预签名URL成功: {response_data}")
            return response_data
        else:
            raise Exception(f"[KimiChat] 获取预签名URL失败: {response.text}")

    def upload_file(self, url, file_path):
        """上传文件到预签名URL"""
        with open(file_path, 'rb') as file:
            response = requests.put(url, data=file)
            if response.status_code != 200:
                raise Exception(f"[KimiChat] 文件上传失败: {response.status_code}")
            logger.debug(f"[KimiChat] 文件上传成功: {response.status_code}")

    def get_image_dimensions(self, file_path):
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                return str(width), str(height)
        except Exception as e:
            logger.error(f"[KimiChat] 获取图片尺寸失败: {e}")
            return "940", "940"

    @ensure_access_token
    def notify_file_upload(self, file_info, file_path=None, is_image=False):
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
        
        logger.debug(f"[KimiChat] 通知文件上传请求: {file_info}")
        response = requests.post(self.file_upload_api, headers=headers, json=file_info)
        if response.status_code == 200:
            response_data = response.json()
            logger.debug(f"[KimiChat] 通知文件上传成功: {response_data}")
            return response_data.get("id")
        else:
            raise Exception(f"[KimiChat] 通知文件上传失败: {response.text}")

    @ensure_access_token
    def parse_process(self, ids):
        """通知服务器开始解析文件"""
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "ids": [ids]
        }
        
        try:
            response = requests.post(
                self.parse_process_api,
                headers=headers,
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                logger.debug("[KimiChat] 通知解析文件成功")
                return True
            else:
                logger.error(f"[KimiChat] 通知解析文件失败: {response.text}")
                return False
        except Exception as e:
            logger.error(f"[KimiChat] 通知解析文件出错: {str(e)}")
            return False

    @ensure_access_token
    def get_recommend_prompt(self, file_id):
        """获取系统推荐的提示词"""
        auth_token = tokens['access_token']
        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json'
        }
        payload = {
            "ids": [file_id]
        }
        
        try:
            response = requests.post(
                "https://kimi.moonshot.cn/api/file/recommend_prompt",
                headers=headers,
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("recommend_prompt", "")
        except Exception as e:
            logger.error(f"[KimiChat] 获取推荐提示词失败: {str(e)}")
        return ""

    def upload(self, filename, filepath):
        try:
            logger.debug(f"[KimiChat] 准备上传文件: {filename}")
            logger.debug(f"[KimiChat] 文件路径: {filepath}")
            
            # 判断是否为图片
            is_image = filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'))
            
            # 1. 获取预签名 URL
            pre_sign_info = self.get_presigned_url(filename, is_image)
            logger.debug(f"[KimiChat] 获取预签名URL响应: {pre_sign_info}")
            
            # 2. 上传文件到预签名 URL
            self.upload_file(pre_sign_info['url'], filepath)
            logger.debug("[KimiChat] 文件上传完成")
            
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
            logger.debug(f"[KimiChat] 获得文件ID: {file_id}")
            
            # 4. 获取系统推荐的提示词
            if file_id:
                recommend_prompt = self.get_recommend_prompt(file_id)
                logger.debug(f"[KimiChat] 系统推荐提示词: {recommend_prompt}")
                
                # 5. 通知开始解析文件(不等待结果)
                self.parse_process(file_id)
                logger.debug("[KimiChat] 已通知开始解析文件")
            
            return file_id
            
        except Exception as e:
            logger.error(f"[KimiChat] 上传过程中发生错误: {str(e)}", exc_info=True)
            return None
