import re


_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC = re.compile(r"\x1b\].*?\x07")
_SKIP_TEXT = (
    "All tools are now trusted",
    "understand the risks",
    "Learn more at",
    "Credits:",
    "/model",
    "/prompts",
    "Did you know",
)


def clean_output(stdout: str, stderr: str) -> str:
    text = stdout.strip() or stderr.strip() or "Kiro 未返回結果"
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    lines = [
        line
        for line in text.splitlines()
        if not any(marker in line.strip() for marker in _SKIP_TEXT)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
