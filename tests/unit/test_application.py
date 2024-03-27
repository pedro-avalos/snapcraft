# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2024 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Unit tests for application classes."""
import json
import os
from textwrap import dedent

import pytest
import yaml
from craft_application import util
from craft_application.commands.lifecycle import PackCommand
from craft_parts.packages import snaps
from craft_providers import bases

from snapcraft import application, services
from snapcraft.models.project import Architecture


@pytest.fixture(
    params=[
        ["amd64"],
        ["riscv64"],
        ["amd64", "riscv64", "s390x"],
        [Architecture(build_on="amd64", build_for="riscv64")],
    ]
)
def architectures(request):
    return request.param


@pytest.mark.parametrize("env_vars", application.MAPPED_ENV_VARS.items())
def test_application_map_build_on_env_var(monkeypatch, env_vars):
    """Test that instantiating the Snapcraft application class will set the value of the
    SNAPCRAFT_* environment variables to CRAFT_*.
    """
    craft_var = env_vars[0]
    snapcraft_var = env_vars[1]
    env_val = "woop"

    monkeypatch.setenv(snapcraft_var, env_val)
    assert os.getenv(craft_var) is None

    snapcraft_services = services.SnapcraftServiceFactory(app=application.APP_METADATA)
    application.Snapcraft(app=application.APP_METADATA, services=snapcraft_services)

    assert os.getenv(craft_var) == env_val
    assert os.getenv(snapcraft_var) == env_val


@pytest.fixture()
def extension_source(default_project):
    source = default_project.marshal()
    source["confinement"] = "strict"
    source["apps"] = {
        "app1": {
            "command": "app1",
            "extensions": ["fake-extension"],
        }
    }
    return source


@pytest.mark.usefixtures("fake_extension")
def test_application_expand_extensions(emitter, monkeypatch, extension_source, new_dir):
    monkeypatch.setenv("CRAFT_DEBUG", "1")

    (new_dir / "snap").mkdir()
    (new_dir / "snap/snapcraft.yaml").write_text(json.dumps(extension_source))

    monkeypatch.setattr("sys.argv", ["snapcraft", "expand-extensions"])
    application.main()
    emitter.assert_message(
        dedent(
            """\
            name: default
            version: '1.0'
            summary: default project
            description: default project
            base: core24
            build-base: devel
            license: MIT
            parts:
                fake-extension/fake-part:
                    plugin: nil
            confinement: strict
            grade: devel
            apps:
                app1:
                    command: app1
                    plugs:
                    - fake-plug
        """
        )
    )


@pytest.mark.usefixtures("fake_extension")
def test_application_build_with_extensions(monkeypatch, extension_source, new_dir):
    """Test that extensions are correctly applied in regular builds."""
    monkeypatch.setenv("CRAFT_DEBUG", "1")

    project_path = new_dir / "snap/snapcraft.yaml"
    (new_dir / "snap").mkdir()
    project_path.write_text(json.dumps(extension_source))

    # Calling a lifecycle command will create a Project. Creating a Project
    # without applying the extensions will fail because the "extensions" field
    # will still be present on the yaml data, so it's enough to run "pull".
    monkeypatch.setattr("sys.argv", ["snapcraft", "pull", "--destructive-mode"])
    app = application.create_app()
    app.run()

    project = app.get_project()
    assert "fake-extension/fake-part" in project.parts


def test_application_managed_core20_fallback(monkeypatch, new_dir, mocker):
    monkeypatch.setenv("CRAFT_DEBUG", "1")
    monkeypatch.setenv("SNAPCRAFT_BUILD_ENVIRONMENT", "managed-host")

    (new_dir / "snap").mkdir()

    mock_legacy_run = mocker.patch("snapcraft_legacy.cli.legacy.legacy_run")
    mock_create_app = mocker.patch.object(application, "create_app")

    application.main()

    mock_create_app.assert_not_called()
    mock_legacy_run.assert_called()


PARSE_INFO_PROJECT = dedent(
    """\
    name: parse-info-project
    base: core24
    build-base: devel

    grade: devel
    confinement: strict
    adopt-info: parse-info-part

    parts:
      parse-info-part:
        plugin: nil
        source: .
        parse-info: [usr/share/metainfo/app.metainfo.xml]
        override-build: |
          craftctl default
          mkdir -p ${CRAFT_PART_INSTALL}/usr/share/metainfo
          cp metainfo.xml ${CRAFT_PART_INSTALL}/usr/share/metainfo/app.metainfo.xml
"""
)


def test_get_project_parse_info(new_dir):
    """Test that parse-info data is correctly extracted and stored when loading
    the project from a YAML file."""
    snap_dir = new_dir / "snap"
    snap_dir.mkdir()
    project_yaml = snap_dir / "snapcraft.yaml"
    project_yaml.write_text(PARSE_INFO_PROJECT)

    app = application.create_app()
    assert app._parse_info == {}

    _project = app.get_project()
    assert app._parse_info == {
        "parse-info-part": ["usr/share/metainfo/app.metainfo.xml"]
    }


APPSTREAM_CONTENTS = dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <!-- Some Comment -->
    <component type="desktop-application">
      <id>io.snapcraft.appstream</id>
      <metadata_license>FSFAP</metadata_license>
      <project_license>GPL-2.0+</project_license>
      <name>Sample app</name>
      <summary>Sample summary</summary>

      <description><p>Sample description</p></description>

      <releases>
        <release version="1.2.3" date="2020-01-01">
          <description>
            <p>Initial release.</p>
          </description>
        </release>
      </releases>
    </component>
    """
)


def test_parse_info_integrated(monkeypatch, mocker, new_dir):
    # Pretend this is an Ubuntu 24.04 system, to match the project's build-base
    mocker.patch.object(
        util, "get_host_base", return_value=bases.BaseName("ubuntu", "24.04")
    )

    # Mock the installation of the core24 snap, as it can currently fail due
    # to network issues and it's not necessary for the test
    mocker.patch.object(snaps, "install_snaps")

    snap_dir = new_dir / "snap"
    snap_dir.mkdir()

    project_yaml = snap_dir / "snapcraft.yaml"
    project_yaml.write_text(PARSE_INFO_PROJECT)

    metainfo_file = new_dir / "metainfo.xml"
    metainfo_file.write_text(APPSTREAM_CONTENTS)

    monkeypatch.setattr("sys.argv", ["snapcraft", "prime", "--destructive-mode"])
    app = application.create_app()
    app.run()

    # Check for the parsed data directly in the generated snap.yaml
    snap_file = new_dir / "prime/meta/snap.yaml"
    snap_yaml = yaml.safe_load(snap_file.read_text())

    assert snap_yaml["summary"] == "Sample summary"
    assert snap_yaml["description"] == "Sample description"
    assert snap_yaml["version"] == "1.2.3"


def test_application_plugins():
    app = application.create_app()
    plugins = app._get_app_plugins()

    # Just do some sanity checks.
    assert "python" in plugins
    assert "kernel" not in plugins


def test_default_command_integrated(monkeypatch, mocker, new_dir):
    """Test that for core24 projects we accept "pack" as the default command."""

    # Pretend this is an Ubuntu 24.04 system, to match the project's build-base
    mocker.patch.object(
        util, "get_host_base", return_value=bases.BaseName("ubuntu", "24.04")
    )

    snap_dir = new_dir / "snap"
    snap_dir.mkdir()

    # The project itself doesn't really matter.
    project_yaml = snap_dir / "snapcraft.yaml"
    project_yaml.write_text(PARSE_INFO_PROJECT)

    mocked_pack_run = mocker.patch.object(PackCommand, "run", return_value=0)

    monkeypatch.setattr("sys.argv", ["snapcraft", "--destructive-mode"])
    app = application.create_app()
    app.run()

    assert mocked_pack_run.called
