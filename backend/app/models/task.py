"""
任务状态管理
用于跟踪长时间运行的任务（如图谱构建）

任务状态持久化到磁盘（uploads/tasks/），以便在 gunicorn 多 worker 之间共享，
并在后端重启后仍可查询进行中的任务。
"""

import fcntl
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass, field

from ..config import Config
from ..utils.locale import t


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"          # 等待中
    PROCESSING = "processing"    # 处理中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败


@dataclass
class Task:
    """任务数据类"""
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0              # 总进度百分比 0-100
    message: str = ""              # 状态消息
    result: Optional[Dict] = None  # 任务结果
    error: Optional[str] = None    # 错误信息
    metadata: Dict = field(default_factory=dict)  # 额外元数据
    progress_detail: Dict = field(default_factory=dict)  # 详细进度信息

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """从字典恢复任务"""
        return cls(
            task_id=data["task_id"],
            task_type=data["task_type"],
            status=TaskStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            progress=data.get("progress", 0),
            message=data.get("message", ""),
            result=data.get("result"),
            error=data.get("error"),
            metadata=data.get("metadata") or {},
            progress_detail=data.get("progress_detail") or {},
        )


class TaskManager:
    """
    任务管理器
    基于文件的跨进程任务状态管理（gunicorn 多 worker 安全）
    """

    TASKS_DIR = os.path.join(Config.UPLOAD_FOLDER, "tasks")

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _ensure_tasks_dir(cls) -> None:
        os.makedirs(cls.TASKS_DIR, exist_ok=True)

    @classmethod
    def _get_task_path(cls, task_id: str) -> str:
        return os.path.join(cls.TASKS_DIR, f"{task_id}.json")

    @classmethod
    def _get_lock_path(cls, task_id: str) -> str:
        return os.path.join(cls.TASKS_DIR, f"{task_id}.lock")

    @classmethod
    def _load_task(cls, task_id: str) -> Optional[Task]:
        path = cls._get_task_path(task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Task.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    @classmethod
    def _save_task(cls, task: Task) -> None:
        cls._ensure_tasks_dir()
        path = cls._get_task_path(task.task_id)
        temp_path = f"{path}.{os.getpid()}.tmp"
        payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)

    @classmethod
    def _list_task_files(cls) -> List[str]:
        cls._ensure_tasks_dir()
        return [
            os.path.join(cls.TASKS_DIR, name)
            for name in os.listdir(cls.TASKS_DIR)
            if name.endswith(".json")
        ]

    def _with_locked_task(self, task_id: str, mutator: Callable[[Task], None]) -> bool:
        """在独占锁下读取、修改并保存任务。"""
        self._ensure_tasks_dir()
        lock_path = self._get_lock_path(task_id)
        with open(lock_path, "a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                task = self._load_task(task_id)
                if task is None:
                    return False
                mutator(task)
                self._save_task(task)
                return True
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        """
        创建新任务

        Args:
            task_type: 任务类型
            metadata: 额外元数据

        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())
        now = datetime.now()

        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        self._save_task(task)
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        return self._load_task(task_id)

    def find_active_task_for_simulation(
        self,
        simulation_id: str,
        task_type: str,
    ) -> Optional[Task]:
        """按 simulation_id 查找仍在进行中的任务（pending/processing）。"""
        for path in self._list_task_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    task = Task.from_dict(json.load(f))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue

            if (
                task.task_type == task_type
                and task.metadata.get("simulation_id") == simulation_id
                and task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            ):
                return task
        return None

    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
    ):
        """
        更新任务状态

        Args:
            task_id: 任务ID
            status: 新状态
            progress: 进度
            message: 消息
            result: 结果
            error: 错误信息
            progress_detail: 详细进度信息
            metadata: 追加到任务上的元数据
        """
        def apply_updates(task: Task) -> None:
            task.updated_at = datetime.now()
            if status is not None:
                task.status = status
            if progress is not None:
                task.progress = progress
            if message is not None:
                task.message = message
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            if progress_detail is not None:
                task.progress_detail = progress_detail
            if metadata is not None:
                task.metadata = {**task.metadata, **metadata}

        self._with_locked_task(task_id, apply_updates)

    def complete_task(self, task_id: str, result: Dict):
        """标记任务完成"""
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message=t("progress.taskComplete"),
            result=result,
        )

    def fail_task(self, task_id: str, error: str):
        """标记任务失败"""
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            message=t("progress.taskFailed"),
            error=error,
        )

    def list_tasks(self, task_type: Optional[str] = None) -> list:
        """列出任务"""
        tasks: List[Task] = []
        for path in self._list_task_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    task = Task.from_dict(json.load(f))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue
            if task_type is None or task.task_type == task_type:
                tasks.append(task)

        return [task.to_dict() for task in sorted(tasks, key=lambda x: x.created_at, reverse=True)]

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务"""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        for path in self._list_task_files():
            task = None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    task = Task.from_dict(json.load(f))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue

            if task.created_at < cutoff and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                task_id = task.task_id
                lock_path = self._get_lock_path(task_id)
                with open(lock_path, "a+", encoding="utf-8") as lock_f:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    finally:
                        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
