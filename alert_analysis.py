#!/usr/bin/env python3
"""Alert analysis runner — shared between webhook and group message handlers."""

import json
import logging
import os
import re
import shutil
import signal
import subprocess

from alert_matcher import ConfigReloader
from dashboard.config_store import ConfigStore

log = logging.getLogger("alert-analysis")

config_reloader = ConfigReloader(ConfigStore())

KIRO_BIN = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
DEFAULT_AGENT = "ec2-alert-analyzer"
DEFAULT_TOOLS = ["execute_bash"]
DEFAULT_TIMEOUT = int(os.environ.get("ALERT_ANALYZE_TIMEOUT", "120"))


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义码和终端控制字符"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z?]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    # 去掉 kiro 的启动横幅（ASCII art logo + trust warning + credits）
    lines = text.split('\n')
    clean = []
    for line in lines:
        stripped = line.strip()
        if 'All tools are now trusted' in stripped or 'understand the risks' in stripped:
            continue
        if 'Learn more at' in stripped and 'kiro.dev' in stripped:
            continue
        if 'Credits:' in stripped and 'Time:' in stripped:
            continue
        if '/model' in stripped and 'to change' in stripped:
            continue
        if '/prompts' in stripped or 'Did you know' in stripped:
            continue
        # 跳过 ASCII art（连续的特殊 Unicode 块字符行）
        if stripped and all(c in '⠀⢀⣴⣶⣦⡀⣾⠁⠈⠙⣿⡆⢰⠋⢸⣇⡿⢻⣧⠹⣷⡄⠘⣆⠻⠿⠟⣠⡁⢹⣼⠇⠸⣄⢁⣤⠉⡇⠃⠂⠐⠒⠲⠶⠤⠖⠛⠏⠗⠞⠝⠜⠚⠘⠙⠑⠊⠉⠋⠌⠍⠎⠏⡏⡇⡆⡅⡄⡃⡂⡁⡀⢿⣿⣽⣻⣺⣹⣸⣷⣵⣳⣲⣱⣰⣯⣮⣭⣬⣫⣪⣩⣨⣧⣥⣤⣣⣢⣡⣠⣟⣞⣝⣜⣛⣚⣙⣘⣗⣖⣕⣔⣓⣒⣑⣐⣏⣎⣍⣌⣋⣊⣉⣈⣇⣆⣅⣄⣃⣂╭╮╰╯│─' for c in stripped):
            continue
        clean.append(line)
    # 去掉首尾空行
    text = '\n'.join(clean).strip()
    # 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def run_alert_analysis(record: dict, context=None) -> tuple[str, str]:
    """触发 Kiro skill 分析并返回结果文本和使用的 agent.

    Args:
        record: 标准化告警记录，至少包含 title、severity、source 等字段。
        context: 多 profile 模式的 ExecutionContext；None 時維持 legacy 全域行為。

    Returns:
        (analysis_message, agent_name)
    """
    matcher = config_reloader.get_matcher()
    action = matcher.match(record)

    if context is not None:
        # 多 profile：Agent／模型／逾時依規格 §12 優先順序；
        # AWS 只來自 ExecutionContext（Alert Mapping 不得覆蓋）
        from multi_profile.group_alerts import resolve_alert_action
        from multi_profile.runtime_env import build_child_env

        resolved = resolve_alert_action(
            action,
            context.profile,
            default_agent=DEFAULT_AGENT,
            default_tools=DEFAULT_TOOLS,
            default_timeout=DEFAULT_TIMEOUT,
            background_model=os.environ.get("BACKGROUND_MODEL", "").strip(),
        )
        agent, tools, timeout = resolved.agent, list(resolved.tools), resolved.timeout
        model = resolved.model
        env = build_child_env(context)
        cwd = context.profile.working_dir
    else:
        # legacy：全域行為不變
        agent = action.get("agent", DEFAULT_AGENT)
        tools = action.get("tools", DEFAULT_TOOLS)
        timeout = action.get("timeout", DEFAULT_TIMEOUT)
        model = os.environ.get("BACKGROUND_MODEL", "").strip() or None
        env = {**os.environ, "NO_COLOR": "1"}
        cwd = os.path.expanduser("~")

    instruction = action.get("instruction")
    if not instruction:
        instruction = "请分析此告警的根因，查询相关指标数据，给出结构化的诊断报告。"

    alert_payload = json.dumps({
        "alert": {
            "source": record.get("source", "prometheus"),
            "event_type": record.get("event_type", "指标异常"),
            "title": record["title"],
            "description": record.get("description", ""),
            "entities": record.get("entities", []),
            "severity": record.get("severity", "medium"),
            "timestamp": record.get("timestamp"),
        },
        "instruction": instruction,
    }, ensure_ascii=False, indent=2)

    log.info(f"触发 Kiro {agent}: {record['title'][:50]}...")
    cmd = [KIRO_BIN, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    cmd.append(alert_payload)

    try:
        # start_new_session=True 讓 kiro-cli 在新進程組運行，
        # timeout 時可用 os.killpg() 殺掉整個進程樹（包括 kiro-cli-chat 和子 shell）
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=cwd, env=env,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        analysis = strip_ansi(stdout.strip() or stderr.strip() or "Kiro 未返回分析结果")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
        except Exception:
            pass
        analysis = f"⏰ Kiro {agent} 分析超时（{timeout}s）"
    except Exception as e:
        analysis = f"❌ Kiro 调用失败: {e}"
        log.exception("Kiro 分析失败")

    header = (
        f"🚨 自动告警分析\n\n"
        f"【告警】{record['title']}\n"
        f"【级别】{record.get('severity', 'medium').upper()}\n"
        f"【来源】{record.get('source', 'prometheus')}\n"
    )
    message = header + "\n" + analysis
    return message, agent
