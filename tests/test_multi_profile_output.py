from multi_profile.output import clean_output


def test_clean_output_removes_ansi_and_kiro_banner():
    stdout = "\x1b[31mAll tools are now trusted\x1b[0m\nanswer\nCredits: 1 Time: 2"

    assert clean_output(stdout, "") == "answer"


def test_clean_output_uses_stderr_and_default_message():
    assert clean_output("", "failure") == "failure"
    assert clean_output("", "") == "Kiro 未返回結果"
