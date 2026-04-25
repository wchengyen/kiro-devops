"""定时任务调度器 - 支持通过飞书消息设置定时执行 Kiro CLI 任务"""
import json
import logging
import threading
import time
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import schedule

log = logging.getLogger("scheduler")

JOBS_FILE = Path(__file__).parent / "scheduled_jobs.json"

# 中文到 schedule 的映射
DAY_MAP = {
    "每天": "every_day",
    "每周一": "monday", "每周二": "tuesday", "每周三": "wednesday",
    "每周四": "thursday", "每周五": "friday", "每周六": "saturday", "每周日": "sunday",
    "工作日": "weekday",
}

# 解析命令的正则：/schedule 每天 09:00 做某事
SCHEDULE_RE = re.compile(
    r"^(每天|每周[一二三四五六日]|工作日)\s+(\d{1,2}:\d{2})\s+(.+)$"
)


@dataclass
class ScheduledJob:
    id: int
    user_id: str
    frequency: str      # 每天 / 每周一 / 工作日
    time_str: str       # HH:MM
    prompt: str         # 要执行的 kiro 指令
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    enabled: bool = True
    notify_target: str = ""
    source_platform: str = "feishu"


class Scheduler:
    def __init__(self, send_fn, kiro_fn):
        """
        send_fn(user_id, text): 主动发飞书消息的函数
        kiro_fn(prompt) -> str: 调用 kiro-cli 的函数
        """
        self._send = send_fn
        self._kiro = kiro_fn
        self._jobs: list[ScheduledJob] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._load()
        self._register_all()
        self._start_runner()

    # ---- 持久化 ----
    def _save(self):
        with open(JOBS_FILE, "w") as f:
            json.dump([asdict(j) for j in self._jobs], f, ensure_ascii=False, indent=2)

    def _load(self):
        if JOBS_FILE.exists():
            try:
                data = json.loads(JOBS_FILE.read_text())
                self._jobs = [ScheduledJob(**d) for d in data]
                self._next_id = max((j.id for j in self._jobs), default=0) + 1
                log.info(f"加载 {len(self._jobs)} 个定时任务")
            except Exception as e:
                log.warning(f"加载定时任务失败: {e}")

    # ---- 注册到 schedule ----
    def _register_job(self, job: ScheduledJob):
        if not job.enabled:
            return
        freq = DAY_MAP.get(job.frequency)
        if not freq:
            return

        def run():
            self._execute_job(job)

        run.__name__ = f"job_{job.id}"  # schedule 需要唯一标识

        if freq == "every_day":
            schedule.every().day.at(job.time_str).do(run).tag(f"job_{job.id}")
        elif freq == "weekday":
            for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
                getattr(schedule.every(), day).at(job.time_str).do(run).tag(f"job_{job.id}")
        else:
            getattr(schedule.every(), freq).at(job.time_str).do(run).tag(f"job_{job.id}")

    def _register_all(self):
        schedule.clear()
        for job in self._jobs:
            self._register_job(job)

    def _execute_job(self, job: ScheduledJob):
        log.info(f"执行定时任务 #{job.id}: {job.prompt[:50]}...")
        target = job.notify_target or job.user_id
        try:
            result = self._kiro(job.prompt)
            self._send(target, f"⏰ 定时任务 #{job.id} 执行结果：\n\n{result}")
        except Exception as e:
            self._send(target, f"❌ 定时任务 #{job.id} 执行失败: {e}")

    # ---- 后台线程 ----
    def _start_runner(self):
        def loop():
            while True:
                schedule.run_pending()
                time.sleep(30)
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        log.info("定时任务调度器已启动")

    # ---- 用户命令处理 ----
    def handle_command(self, user_id: str, text: str, source_platform: str = "feishu") -> str:
        """处理 /schedule 命令，返回回复文本"""
        text = text.strip()

        if text in ("list", "列表", "ls"):
            return self._list(user_id)

        if text.startswith(("delete ", "删除 ", "del ", "rm ")):
            return self._delete(user_id, text.split(None, 1)[1])

        m = SCHEDULE_RE.match(text)
        if not m:
            return (
                "📅 定时任务用法：\n\n"
                "/schedule 每天 09:00 检查EC2实例状态\n"
                "/schedule 每周一 10:00 生成WBR报告\n"
                "/schedule 工作日 08:30 查看CloudWatch告警\n"
                "/schedule list — 查看任务列表\n"
                "/schedule delete 1 — 删除任务"
            )

        freq, time_str, prompt = m.group(1), m.group(2), m.group(3)

        # 验证时间格式
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return "❌ 时间格式错误，请使用 HH:MM（如 09:00）"

        with self._lock:
            job = ScheduledJob(
                id=self._next_id, user_id=user_id,
                frequency=freq, time_str=time_str, prompt=prompt,
                notify_target=user_id, source_platform=source_platform,
            )
            self._jobs.append(job)
            self._next_id += 1
            self._register_job(job)
            self._save()

        return f"✅ 定时任务 #{job.id} 已创建\n频率：{freq} {time_str}\n指令：{prompt}"

    def _list(self, user_id: str) -> str:
        user_jobs = [j for j in self._jobs if j.user_id == user_id and j.enabled]
        if not user_jobs:
            return "📭 你还没有定时任务"
        lines = ["📅 你的定时任务：\n"]
        for j in user_jobs:
            lines.append(f"  #{j.id} | {j.frequency} {j.time_str} | {j.prompt}")
        return "\n".join(lines)

    def _delete(self, user_id: str, id_str: str) -> str:
        try:
            job_id = int(id_str.strip().lstrip("#"))
        except ValueError:
            return "❌ 请输入任务编号，如：/schedule delete 1"

        with self._lock:
            for j in self._jobs:
                if j.id == job_id and j.user_id == user_id:
                    j.enabled = False
                    schedule.clear(f"job_{job_id}")
                    self._save()
                    return f"🗑️ 定时任务 #{job_id} 已删除"
        return f"❌ 未找到任务 #{job_id}"

    # ---- Dashboard CRUD API ----
    def list_jobs(self, user_id: str):
        """Return list of job dicts.  user_id='all' returns every job."""
        with self._lock:
            if user_id == "all":
                jobs = self._jobs[:]
            else:
                jobs = [j for j in self._jobs if j.user_id == user_id]
            return [asdict(j) for j in jobs]

    def add_job(self, user_id: str, frequency: str, time_str: str, prompt: str) -> int:
        """Create a new scheduled job and return its id."""
        with self._lock:
            job = ScheduledJob(
                id=self._next_id,
                user_id=user_id,
                frequency=frequency,
                time_str=time_str,
                prompt=prompt,
            )
            self._jobs.append(job)
            self._next_id += 1
            self._register_job(job)
            self._save()
            return job.id

    def enable_job(self, job_id: int) -> bool:
        with self._lock:
            for j in self._jobs:
                if j.id == job_id:
                    j.enabled = True
                    self._register_job(j)
                    self._save()
                    return True
            return False

    def disable_job(self, job_id: int) -> bool:
        with self._lock:
            for j in self._jobs:
                if j.id == job_id:
                    j.enabled = False
                    schedule.clear(f"job_{job_id}")
                    self._save()
                    return True
            return False

    def edit_job(self, job_id: int, updates: dict) -> bool:
        with self._lock:
            for j in self._jobs:
                if j.id == job_id:
                    if "frequency" in updates:
                        j.frequency = updates["frequency"]
                    if "time_str" in updates:
                        j.time_str = updates["time_str"]
                    if "prompt" in updates:
                        j.prompt = updates["prompt"]
                    if j.enabled:
                        schedule.clear(f"job_{job_id}")
                        self._register_job(j)
                    self._save()
                    return True
            return False

    def delete_job(self, job_id: int) -> bool:
        with self._lock:
            for idx, j in enumerate(self._jobs):
                if j.id == job_id:
                    schedule.clear(f"job_{job_id}")
                    self._jobs.pop(idx)
                    self._save()
                    return True
            return False
