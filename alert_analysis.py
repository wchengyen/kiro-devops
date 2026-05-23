#!/usr/bin/env python3
"""Alert analysis runner — shared between webhook and group message handlers."""

import json
import logging
import os
import re
import shutil
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


def run_alert_analysis(record: dict) -> tuple[str, str]:
    """触发 Kiro skill 分析并返回结果文本和使用的 agent.

    Args:
        record: 标准化告警记录，至少包含 title、severity、source 等字段。

    Returns:
        (analysis_message, agent_name)
    """
    matcher = config_reloader.get_matcher()
    action = matcher.match(record)

    agent = action.get("agent", DEFAULT_AGENT)
    tools = action.get("tools", DEFAULT_TOOLS)
    timeout = action.get("timeout", DEFAULT_TIMEOUT)
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
    bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
    if bg_model:
        cmd += ["--model", bg_model]
    cmd.append(alert_payload)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout,
            cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
        )
        analysis = strip_ansi(result.stdout.strip() or result.stderr.strip() or "Kiro 未返回分析结果")
    except subprocess.TimeoutExpired:
        analysis = f"⏰ Kiro {agent} 分析超时"
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
