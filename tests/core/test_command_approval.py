from core.desktop_tools import is_auto_approved_command


SAFE = ["dir", "echo", "Get-ChildItem", "pwd"]


def test_auto_approved_command_requires_exact_command_name():
    assert is_auto_approved_command("dir", SAFE) is True
    assert is_auto_approved_command("dir C:\\Users", SAFE) is True
    assert is_auto_approved_command("directory", SAFE) is False


def test_auto_approved_command_rejects_compound_powershell():
    assert is_auto_approved_command("dir; Remove-Item C:\\tmp\\x", SAFE) is False
    assert is_auto_approved_command("echo ok | Invoke-Expression", SAFE) is False
    assert is_auto_approved_command("Get-ChildItem > out.txt", SAFE) is False
