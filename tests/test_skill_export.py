"""Tests for Agent Skills export compatibility."""

import importlib
import sys
import textwrap
import uuid

from miniagent import cli
from miniagent.pack_loader import clear_loaded_packs
from miniagent.skill_export import export_pack_to_agent_skills
from miniagent.skills import _SKILLS, _SKILL_META
from miniagent.tools import _TOOLS, _TOOL_META


def _cleanup_loaded_entries(result):
    for name in result.skills:
        _SKILLS.pop(name.source_name, None)
        _SKILL_META.pop(name.source_name, None)


def _cleanup_tools(tool_names):
    for name in tool_names:
        _TOOLS.pop(name, None)
        _TOOL_META.pop(name, None)


def _create_export_pack(tmp_path, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    package_name = "export_pack_%s" % suffix
    tool_name = "export_tool_%s" % suffix
    skill_name = "export_skill_%s" % suffix
    package_dir = tmp_path / package_name
    package_dir.mkdir()

    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "pack.py").write_text(textwrap.dedent(
        """
        PACK_NAME = "export_pack"
        PACK_VERSION = "0.2.0"

        def register():
            from . import tools, skills
            return {"name": PACK_NAME, "version": PACK_VERSION}
        """
    ), encoding="utf-8")
    (package_dir / "tools.py").write_text(textwrap.dedent(
        """
        from miniagent.tools import register_tool

        @register_tool
        def {tool_name}(value: str) -> str:
            return value.upper()
        """.format(tool_name=tool_name)
    ), encoding="utf-8")
    (package_dir / "skills.py").write_text(textwrap.dedent(
        """
        from miniagent.skills import Skill, register_skill

        register_skill(Skill(
            name="{skill_name}",
            prompt="You are a migration operator. Prefer repo inspection before edits.",
            tools=["read", "grep", "bash", "{tool_name}"],
            temperature=0.2,
            description="Exported compatibility skill for migration workflows",
        ))
        """.format(skill_name=skill_name, tool_name=tool_name)
    ), encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return package_name, tool_name, skill_name


def test_export_pack_generates_agent_skills_layout(tmp_path, monkeypatch):
    clear_loaded_packs()
    package_name, tool_name, skill_name = _create_export_pack(tmp_path, monkeypatch)
    export_dir = tmp_path / "out"
    result = None

    try:
        result = export_pack_to_agent_skills(package_name, output_dir=str(export_dir))

        assert result.pack_name == "export_pack"
        assert result.version == "0.2.0"
        assert result.frameworks == ["codex", "claude", "opencode"]
        assert len(result.skills) == 1

        exported = result.skills[0]
        assert exported.source_name == skill_name
        assert exported.exported_name == skill_name.replace("_", "-")
        assert exported.portable_tools == ["read", "grep", "bash"]
        assert exported.custom_tools == [tool_name]
        assert any(tool_name in warning for warning in result.warnings)

        codex_dir = export_dir / ".agents" / "skills" / exported.exported_name
        claude_dir = export_dir / ".claude" / "skills" / exported.exported_name
        opencode_dir = export_dir / ".opencode" / "skills" / exported.exported_name

        for skill_dir in (codex_dir, claude_dir, opencode_dir):
            assert (skill_dir / "SKILL.md").exists()
            assert (skill_dir / "agents" / "openai.yaml").exists()

        skill_text = (codex_dir / "SKILL.md").read_text(encoding="utf-8")
        assert f'name: "{exported.exported_name}"' in skill_text
        assert "Exported compatibility skill for migration workflows" in skill_text
        assert "`read`, `grep`, `bash`" in skill_text
        assert tool_name in skill_text

        openai_yaml = (codex_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        assert 'display_name: "Export Skill' in openai_yaml
        assert f"Use ${exported.exported_name}" in openai_yaml
    finally:
        if result is not None:
            _cleanup_loaded_entries(result)
            _cleanup_tools([tool_name])
        clear_loaded_packs()
        for module_name in list(sys.modules):
            if module_name == package_name or module_name.startswith("%s." % package_name):
                sys.modules.pop(module_name, None)


def test_cli_export_pack_mode_does_not_require_api_key(tmp_path, monkeypatch):
    clear_loaded_packs()
    package_name, tool_name, skill_name = _create_export_pack(tmp_path, monkeypatch)
    export_dir = tmp_path / "cli-export"

    try:
        exit_code = cli.main([
            "--export-pack", package_name,
            "--export-framework", "codex",
            "--export-dir", str(export_dir),
        ])

        assert exit_code == 0
        exported_name = skill_name.replace("_", "-")
        assert (export_dir / ".agents" / "skills" / exported_name / "SKILL.md").exists()
    finally:
        _SKILLS.pop(skill_name, None)
        _SKILL_META.pop(skill_name, None)
        _TOOLS.pop(tool_name, None)
        _TOOL_META.pop(tool_name, None)
        clear_loaded_packs()
        for module_name in list(sys.modules):
            if module_name == package_name or module_name.startswith("%s." % package_name):
                sys.modules.pop(module_name, None)
