from __future__ import annotations

import json
import tomllib
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parents[1]
SITE = ROOT / "site"


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = "href" if tag in {"a", "link"} else "src" if tag in {"img", "script"} else None
        if attribute and values.get(attribute):
            self.references.append(values[attribute] or "")


def test_release_versions_are_consistent() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        version = tomllib.load(project_file)["project"]["version"]
    package_version = json.loads((ROOT / "package.json").read_text())["version"]

    assert version == "1.0.0"
    assert package_version == version
    assert f'DEFAULT_VERSION="v{version}"' in (ROOT / "install.sh").read_text()
    assert f'version="{version}"' in (ROOT / "src" / "pi_beacon" / "api.py").read_text()
    assert f"Version {version}" in (SITE / "changelog.html").read_text()


def test_pi_package_metadata() -> None:
    package = json.loads((ROOT / "package.json").read_text())
    assert package["name"] == "pi-beacon"
    assert "pi-package" in package["keywords"]
    assert package["pi"]["extensions"] == ["./extension/index.ts"]
    assert package["pi"]["image"].endswith("pi-beacon-dashboard.png")
    assert (ROOT / "extension" / "index.ts").is_file()


def test_quickshell_assets_are_customizable() -> None:
    panel = ROOT / "src" / "pi_beacon" / "assets" / "quickshell" / "PiBeaconPanel.qml"
    theme = ROOT / "src" / "pi_beacon" / "assets" / "quickshell" / "PiBeaconTheme.qml"
    assert panel.is_file()
    assert theme.is_file()
    panel_source = panel.read_text()
    assert "property var theme: defaultTheme" in panel_source
    assert 'root.streamExecutable, "--format", "snapshot"' in panel_source
    assert 'property string name: "Zen"' in theme.read_text()


def test_service_and_migrations_are_packaged() -> None:
    package = ROOT / "src" / "pi_beacon"
    assert (package / "assets" / "systemd" / "pi-beacon.service").is_file()
    assert (package / "migrations" / "env.py").is_file()
    assert (package / "migrations" / "versions" / "0001_initial.py").is_file()
    assert (package / "migrations" / "versions" / "0002_source_identity.py").is_file()


def test_landing_page_has_no_dead_local_assets() -> None:
    parser = AssetParser()
    parser.feed((SITE / "index.html").read_text())
    for reference in parser.references:
        if reference.startswith(("http://", "https://", "#")):
            continue
        path = reference.split("?", 1)[0]
        assert (SITE / path).exists(), reference
    assert 'href="#"' not in (SITE / "index.html").read_text()
    assert "innerHTML" not in (SITE / "main.js").read_text()


def test_required_static_pages_exist() -> None:
    for name in ("404.html", "changelog.html", "privacy.html", "terms.html", ".nojekyll"):
        assert (SITE / name).exists()
