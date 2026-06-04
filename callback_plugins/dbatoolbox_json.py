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

    def v2_playbook_on_play_start(self, play):
        self._current_play = {"play": {"name": play.get_name()}, "tasks": []}
        self._plays.append(self._current_play)

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._current_task = {"task": {"name": task.get_name()}, "hosts": {}}
        if self._current_play is not None:
            self._current_play["tasks"].append(self._current_task)

    def _add_host_result(self, host_result, hostname, unreachable=False, failed=False):
        data = host_result._result.copy()
        data.setdefault("unreachable", unreachable)
        data.setdefault("failed", failed)
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
