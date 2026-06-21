from abc import ABC, abstractmethod

class BaseEngine(ABC):
    """
    Abstract Base Class สำหรับทุก Engine ในระบบ
    ไม่มี Logic ใด ๆ ทั้งสิ้น — เป็นเพียง Interface บังคับ
    """
    @abstractmethod
    def run(self, *args, **kwargs):
        pass
