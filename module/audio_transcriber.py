import requests
import logging
import os

logger = logging.getLogger(__name__)

class AudioTranscriber:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_url = "https://api.siliconflow.cn/v1/audio/transcriptions"
        
    def transcribe(self, audio_path):
        """将音频文件转换为文字"""
        try:
            if not os.path.exists(audio_path):
                raise FileNotFoundError(f"音频文件不存在: {audio_path}")
            
            # 准备请求数据
            with open(audio_path, 'rb') as audio_file:
                files = {
                    'file': ('audio.wav', audio_file),
                    'model': (None, 'FunAudioLLM/SenseVoiceSmall')
                }
                
                headers = {
                    'Authorization': f'Bearer {self.api_key}'
                }
                
                # 发送请求
                response = requests.post(
                    self.api_url,
                    files=files,
                    headers=headers,
                    timeout=30  # 设置超时时间
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get('text', '')
                    if text:
                        logger.info("音频转文字成功")
                        return text
                    else:
                        logger.warning("音频转文字结果为空")
                        return None
                else:
                    logger.error(f"音频转文字请求失败: {response.text}")
                    return None
                    
        except FileNotFoundError as e:
            logger.error(f"音频文件不存在: {str(e)}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"音频转文字请求异常: {str(e)}")
            return None  
        except Exception as e:
            logger.error(f"音频转文字出错: {str(e)}")
            return None
        finally:
            # 确保文件被关闭
            if 'audio_file' in locals():
                audio_file.close() 