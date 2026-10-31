from pathlib import Path
from types import SimpleNamespace
import sys

from src.autostart import AutostartService, WindowsRunRegistry, get_autostart_service


class FakeRegistry:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = None


def test_enable_generates_portable_quoted_launcher(tmp_path: Path):
    project = tmp_path / "项目 with spaces"
    project.mkdir()
    registry = FakeRegistry()
    service = AutostartService(
        project_dir=project,
        executable=Path("C:/Python With Spaces/python.exe"),
        registry=registry,
        wscript=Path("C:/Windows/System32/wscript.exe"),
    )

    launcher = service.enable()

    assert launcher.read_bytes().startswith(b"\xff\xfe")
    content = launcher.read_text(encoding="utf-16")
    assert str(project) in content
    assert "C:\\Python With Spaces\\python.exe" in content
    assert "pythonw.exe" not in content
    assert "-m src.main run" in content
    assert registry.value == f'"C:\\Windows\\System32\\wscript.exe" "{launcher}"'
    assert service.is_enabled() is True


def test_disable_is_idempotent_and_removes_generated_launcher(tmp_path: Path):
    registry = FakeRegistry()
    service = AutostartService(tmp_path, Path("C:/Python/python.exe"), registry,
                               Path("C:/Windows/System32/wscript.exe"))
    launcher = service.enable()

    service.disable()
    service.disable()

    assert registry.value is None
    assert not launcher.exists()


def test_default_service_uses_current_project_and_windows_script(monkeypatch):
    monkeypatch.setenv("SystemRoot", "D:/Windows")

    service = get_autostart_service()

    assert service.project_dir == Path(__file__).resolve().parent.parent
    assert service.wscript == Path("D:/Windows/System32/wscript.exe")


def test_windows_registry_adapter_handles_missing_set_get_and_delete(monkeypatch):
    values = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def open_key(*args):
        if "PCTimeTracker" not in values and args[-1] == 1:
            raise FileNotFoundError
        return Key()

    fake = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_READ=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        OpenKey=open_key,
        CreateKeyEx=lambda *args: Key(),
        QueryValueEx=lambda key, name: (values[name], 1),
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
        DeleteValue=lambda key, name: values.pop(name) if name in values
        else (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    registry = WindowsRunRegistry()

    assert registry.get() is None
    registry.set("command")
    assert registry.get() == "command"
    registry.delete()
    registry.delete()
