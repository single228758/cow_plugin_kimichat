import os
import cv2
import numpy as np
from datetime import datetime
import logging
import traceback
from typing import List, Optional, Tuple, Union
from pathlib import Path
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class VideoFrameManager:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.cap = None

    def extract_frames(self, video_path, max_frames=50):
        """
        从视频中提取帧
        Args:
            video_path: 视频文件路径 
            max_frames: 最大提取帧数
        Returns:
            list of tuples: [(frame_path, timestamp), ...]
        """
        try:
            self.cap = cv2.VideoCapture(video_path)
            if not self.cap.isOpened():
                logger.error("[KimiChat] 无法打开视频文件")
                return []
            
            frames = []
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps
            
            if duration <= 50:
                # 50秒以内的视频,每秒提取一帧
                frame_interval = int(fps)
            else:
                # 50秒以上的视频,均匀分配50张帧
                frame_interval = int(total_frames / max_frames)
                # 确保帧间隔至少为1
                frame_interval = max(1, frame_interval)
            
            frame_count = 0
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    break
                    
                if frame_count % frame_interval == 0:
                    timestamp = frame_count / fps
                    frame_path = os.path.join(
                        self.output_dir,
                        f'frame_{int(timestamp)}_{int(time.time())}.jpg'
                    )
                    cv2.imwrite(frame_path, frame)
                    frames.append((frame_path, timestamp))
                    
                    if len(frames) >= max_frames:
                        break
                    
                frame_count += 1
                
            return frames
            
        except Exception as e:
            logger.error(f"[KimiChat] 提取视频帧失败: {e}")
            return []
        finally:
            if self.cap:
                self.cap.release()
