import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "telethon" not in sys.modules:
    telethon_stub = types.ModuleType("telethon")
    telethon_stub.types = types.SimpleNamespace(DialogFilter=type("DialogFilter", (), {}), PeerChannel=type("PeerChannel", (), {}))
    telethon_stub.utils = types.SimpleNamespace(get_peer_id=lambda peer, add_mark=True: 0, resolve_id=lambda chat_id: (abs(int(chat_id)), None))
    sys.modules["telethon"] = telethon_stub

from tgr.core.plugin_system import PluginManager


class DummyConfig:
    def __init__(self, work_dir: Path, plugins_root: Path) -> None:
        self.work_dir = work_dir
        self.plugins_root = plugins_root
        self.configs_dir = work_dir / "configs"
        self.logs_dir = work_dir / "runtime" / "logs"
        self.cmd_prefix = "-"


class DummyApp:
    def __init__(self, work_dir: Path, plugins_root: Path) -> None:
        self.config = DummyConfig(work_dir, plugins_root)
        self.db = None


class PluginManagerExternalPluginsCheckTests(unittest.TestCase):
    def test_validate_external_plugins_logs_error_when_dir_has_no_loadable_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            plugins_root = work_dir / "plugins-external" / "TG-Radar-Plugins" / "plugins"
            plugins_root.mkdir(parents=True)
            app = DummyApp(work_dir, plugins_root)
            manager = PluginManager(app)

            manager.load_admin_plugins()
            manager.load_core_plugins()

            with self.assertLogs("tgr.plugin_system", level="ERROR") as captured:
                issues = manager.validate_external_plugins()

            self.assertTrue(any("外部插件目录为空或不可见" in issue for issue in issues))
            self.assertTrue(any(str(plugins_root) in issue for issue in issues))
            self.assertTrue(any("外部插件目录为空或不可见" in entry for entry in manager.load_errors))
            self.assertTrue(any(str(plugins_root) in line for line in captured.output))

    def test_validate_external_plugins_is_quiet_when_loadable_plugin_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            plugins_root = work_dir / "plugins-external" / "TG-Radar-Plugins" / "plugins"
            (plugins_root / "admin").mkdir(parents=True)
            (plugins_root / "admin" / "demo.py").write_text(
                "PLUGIN_META = {'name': 'demo', 'version': '1.0.0', 'description': 'demo', 'kind': 'admin'}\n"
                "def setup(ctx):\n"
                "    return None\n",
                encoding="utf-8",
            )
            app = DummyApp(work_dir, plugins_root)
            manager = PluginManager(app)

            manager.load_admin_plugins()
            manager.load_core_plugins()

            with self.assertNoLogs("tgr.plugin_system", level="ERROR"):
                issues = manager.validate_external_plugins()

            self.assertEqual([], issues)
            self.assertIn("demo", manager.plugins)
            self.assertEqual([], [entry for entry in manager.load_errors if "外部插件目录为空或不可见" in entry])

    def test_validate_external_plugins_rejects_repo_root_without_admin_or_core_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            plugins_root = work_dir / "plugins-external" / "TG-Radar-Plugins"
            (plugins_root / "plugins" / "admin").mkdir(parents=True)
            (plugins_root / "plugins" / "admin" / "demo.py").write_text(
                "PLUGIN_META = {'name': 'demo', 'version': '1.0.0', 'description': 'demo', 'kind': 'admin'}\n"
                "def setup(ctx):\n"
                "    return None\n",
                encoding="utf-8",
            )
            app = DummyApp(work_dir, plugins_root)
            manager = PluginManager(app)

            manager.load_admin_plugins()
            manager.load_core_plugins()

            with self.assertLogs("tgr.plugin_system", level="ERROR") as captured:
                issues = manager.validate_external_plugins()

            self.assertTrue(any("外部插件目录为空或不可见" in issue for issue in issues))
            self.assertTrue(any(str(plugins_root) in issue for issue in issues))
            self.assertTrue(any(str(plugins_root) in line for line in captured.output))
            self.assertNotIn("demo", manager.plugins)


if __name__ == "__main__":
    unittest.main()
