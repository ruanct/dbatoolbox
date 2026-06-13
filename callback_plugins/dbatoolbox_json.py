"""极简 JSON stdout callback，替代 ansible-core 缺失的 json callback 插件。"""
import json as _json

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    callback: dbatoolbox_json
    type: stdout
    short_description: Minimal JSON stdout callback for dbatoolbox
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "dbatoolbox_json"

    def __init__(self):
        super().__init__()
        self._current_play = None
        self._current_task = None
        self._plays = []
        self._task_start = None  # 当前 task 的开始时间

    def v2_playbook_on_play_start(self, play):
        self._current_play = {"play": {"name": play.get_name()}, "tasks": []}
        self._plays.append(self._current_play)

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._current_task = {"task": {"name": task.get_name()}, "hosts": {}}
        # 记录 task 开始时间，作为该 task 下所有主机的大致开始时间
        import datetime as _dt
        self._task_start = _dt.datetime.now()
        if self._current_play is not None:
            self._current_play["tasks"].append(self._current_task)

    def _add_host_result(self, host_result, hostname, unreachable=False, failed=False):
        import datetime as _dt
        data = host_result._result.copy()
        data.setdefault("unreachable", unreachable)
        data.setdefault("failed", failed)
        now_ts = _dt.datetime.now()

        # 优先使用 ansible 自带的 start/end/delta（shell 等普通模块有）
        # 对于 script 等 action plugin，这些字段不存在，用 callback 记录的时间兜底
        tf = host_result._task_fields
        for key in ("start", "end", "delta"):
            v = tf.get(key) if tf else None
            if v is None:
                continue
            if hasattr(v, "isoformat"):
                data[key] = v.isoformat()
            elif hasattr(v, "total_seconds"):
                total_secs = int(v.total_seconds())
                hours, rem = divmod(total_secs, 3600)
                mins, secs = divmod(rem, 60)
                data[key] = f"{hours}:{mins:02d}:{secs:02d}.{v.microseconds:06d}"

        # 兜底：用 task 开始时间做 start，回调时间做 end，二者差值为耗时
        if "start" not in data or data.get("start") is None:
            data["start"] = (self._task_start or now_ts).isoformat()
        if "end" not in data or data.get("end") is None:
            data["end"] = now_ts.isoformat()
        if "delta" not in data or data.get("delta") in (None, ""):
            task_start = self._task_start or now_ts
            td = now_ts - task_start
            total_secs = int(td.total_seconds())
            hours, rem = divmod(total_secs, 3600)
            mins, secs = divmod(rem, 60)
            data["delta"] = f"{hours}:{mins:02d}:{secs:02d}.{td.microseconds:06d}"

        if self._current_task is not None:
            self._current_task["hosts"][hostname] = data

    def v2_runner_on_ok(self, result):
        self._add_host_result(result, result._host.get_name())

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._add_host_result(result, result._host.get_name(), failed=True)

    def v2_runner_on_unreachable(self, result):
        self._add_host_result(result, result._host.get_name(), unreachable=True)

    def v2_playbook_on_stats(self, stats):
        output = {"plays": self._plays, "stats": {}}
        for hostname in sorted(stats.processed):
            s = stats.summarize(hostname)
            output["stats"][hostname] = s
        self._display.display(_json.dumps(output))
