"""Export MiniAgent packs to Agent Skills standard directories."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .pack_loader import load_pack
from .skills import Skill, get_skill


_FRAMEWORK_ROOTS = {
    "codex": (".agents", "skills"),
    "claude": (".claude", "skills"),
    "opencode": (".opencode", "skills"),
}

_PORTABLE_TOOL_NAMES = {
    "bash",
    "read",
    "write",
    "edit",
    "grep",
    "glob",
}


@dataclass
class ExportedSkill:
    """Details about one exported skill."""

    source_name: str
    exported_name: str
    description: str
    portable_tools: List[str] = field(default_factory=list)
    custom_tools: List[str] = field(default_factory=list)
    output_paths: Dict[str, str] = field(default_factory=dict)


@dataclass
class PackExportResult:
    """Summary of one pack export run."""

    spec: str
    pack_name: str
    version: str = ""
    output_root: str = ""
    frameworks: List[str] = field(default_factory=list)
    skills: List[ExportedSkill] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _slugify(value: str) -> str:
    """Normalize a skill name for cross-framework folder usage."""

    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:63] or "skill"


def _dedupe_skill_names(skills: Sequence[str]) -> Dict[str, str]:
    """Return a stable mapping from source skill names to exported names."""

    counts: Dict[str, int] = {}
    mapping: Dict[str, str] = {}
    for name in skills:
        base = _slugify(name)
        index = counts.get(base, 0)
        counts[base] = index + 1
        mapping[name] = base if index == 0 else f"{base}-{index + 1}"
    return mapping


def _quote_yaml(value: str) -> str:
    """Return a JSON-quoted scalar that is also valid YAML."""

    return json.dumps(value, ensure_ascii=False)


def _shorten(text: str, limit: int) -> str:
    """Trim text to a target length without breaking the output format."""

    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _title_from_name(name: str) -> str:
    """Build a human-friendly display title from a skill name."""

    parts = [part for part in re.split(r"[-_]+", name) if part]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or name


def _build_frontmatter_description(skill: Skill) -> str:
    """Build a trigger-friendly skill description for SKILL.md."""

    if skill.description.strip():
        return skill.description.strip()

    prompt = " ".join(skill.prompt.split())
    first_sentence = prompt.split(".")[0].strip()
    if first_sentence:
        return (
            f"{first_sentence}. Use when this role, workflow, or tool preference "
            f"matches the current task."
        )
    return f"Use when tasks match the '{skill.name}' role exported from MiniAgent."


def _split_tools(
    skill: Skill,
    pack_tool_names: Iterable[str],
) -> Tuple[List[str], List[str], List[str]]:
    """Split tools into portable, pack-local, and other non-pack names."""

    tool_names = list(skill.tools or [])
    pack_tool_set = set(pack_tool_names)
    portable = [name for name in tool_names if name in _PORTABLE_TOOL_NAMES]
    custom = [name for name in tool_names if name in pack_tool_set]
    other = [
        name for name in tool_names
        if name not in _PORTABLE_TOOL_NAMES and name not in pack_tool_set
    ]
    return portable, custom, other


def _build_skill_markdown(
    exported_name: str,
    skill: Skill,
    *,
    pack_name: str,
    pack_spec: str,
    portable_tools: Sequence[str],
    custom_tools: Sequence[str],
    other_tools: Sequence[str],
) -> str:
    """Render one SKILL.md file."""

    description = _build_frontmatter_description(skill)
    lines = [
        "---",
        f"name: {_quote_yaml(exported_name)}",
        f"description: {_quote_yaml(description)}",
        "---",
        "",
        f"# {_title_from_name(exported_name)}",
        "",
        "## Role",
        "Follow this role definition:",
        "```text",
        skill.prompt.strip(),
        "```",
        "",
    ]

    if skill.tools:
        lines.extend(["## Tool Preference"])
        if portable_tools:
            lines.append(
                "- Prefer these commonly portable tools when the current framework provides equivalents: %s."
                % ", ".join(f"`{name}`" for name in portable_tools)
            )
        if custom_tools:
            lines.append(
                "- Expect these MiniAgent pack tools when available: %s."
                % ", ".join(f"`{name}`" for name in custom_tools)
            )
            lines.append(
                "- If those pack-specific tools do not exist in the current framework, use the closest built-in tool or a shell/Python fallback and state the substitution."
            )
        if other_tools:
            lines.append(
                "- Map these framework-specific tool expectations to local equivalents when needed: %s."
                % ", ".join(f"`{name}`" for name in other_tools)
            )
        lines.append("")

    lines.extend([
        "## Source Metadata",
        f"- Source pack: `{pack_name}` (`{pack_spec}`)",
        f"- Source MiniAgent skill: `{skill.name}`",
    ])
    if skill.temperature is not None:
        lines.append(f"- MiniAgent temperature hint: `{skill.temperature}`")
    if skill.max_iterations is not None:
        lines.append(f"- MiniAgent max-iterations hint: `{skill.max_iterations}`")

    return "\n".join(lines).rstrip() + "\n"


def _build_openai_yaml(exported_name: str, description: str) -> str:
    """Render a minimal Codex-specific agents/openai.yaml file."""

    display_name = _title_from_name(exported_name)
    short_description = _shorten(description, 64) or display_name
    default_prompt = (
        f"Use ${exported_name} to handle this task with the exported role and tool guidance."
    )
    lines = [
        "interface:",
        f"  display_name: {_quote_yaml(display_name)}",
        f"  short_description: {_quote_yaml(short_description)}",
        f"  default_prompt: {_quote_yaml(default_prompt)}",
        "",
        "policy:",
        "  allow_implicit_invocation: true",
        "",
    ]
    return "\n".join(lines)


def _frameworks_to_export(frameworks: Optional[Sequence[str]]) -> List[str]:
    """Normalize requested framework names."""

    if not frameworks:
        return ["codex", "claude", "opencode"]

    normalized: List[str] = []
    for item in frameworks:
        key = item.strip().lower()
        if key in ("claude-code", "claude"):
            key = "claude"
        if key == "all":
            for value in ("codex", "claude", "opencode"):
                if value not in normalized:
                    normalized.append(value)
            continue
        if key not in _FRAMEWORK_ROOTS:
            raise ValueError(
                "Unsupported export framework '%s'. Expected one of: codex, claude, opencode."
                % item
            )
        if key not in normalized:
            normalized.append(key)
    return normalized


def export_pack_to_agent_skills(
    spec: str,
    *,
    output_dir: str,
    frameworks: Optional[Sequence[str]] = None,
) -> PackExportResult:
    """Export one MiniAgent pack to Agent Skills directory layouts."""

    result = load_pack(spec, strict=True)
    active_frameworks = _frameworks_to_export(frameworks)
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if not result.module:
        raise ValueError("Pack '%s' could not be resolved for export." % spec)
    if not result.skills:
        raise ValueError("Pack '%s' did not register any skills to export." % spec)

    name_map = _dedupe_skill_names(result.skills)
    export_result = PackExportResult(
        spec=spec,
        pack_name=result.pack_name,
        version=result.version,
        output_root=str(output_root),
        frameworks=active_frameworks,
    )

    for framework in active_frameworks:
        root = output_root.joinpath(*_FRAMEWORK_ROOTS[framework])
        root.mkdir(parents=True, exist_ok=True)

    for skill_name in result.skills:
        skill = get_skill(skill_name)
        if skill is None:
            raise ValueError("Skill '%s' disappeared during export." % skill_name)

        exported_name = name_map[skill_name]
        portable_tools, custom_tools, other_tools = _split_tools(skill, result.tools)
        description = _build_frontmatter_description(skill)
        exported = ExportedSkill(
            source_name=skill.name,
            exported_name=exported_name,
            description=description,
            portable_tools=portable_tools,
            custom_tools=custom_tools,
        )

        if custom_tools:
            export_result.warnings.append(
                "Skill '%s' depends on pack-specific tools: %s"
                % (skill.name, ", ".join(custom_tools))
            )
        if other_tools:
            export_result.warnings.append(
                "Skill '%s' references non-portable tools that must be mapped manually: %s"
                % (skill.name, ", ".join(other_tools))
            )

        skill_markdown = _build_skill_markdown(
            exported_name,
            skill,
            pack_name=result.pack_name,
            pack_spec=spec,
            portable_tools=portable_tools,
            custom_tools=custom_tools,
            other_tools=other_tools,
        )
        openai_yaml = _build_openai_yaml(exported_name, description)

        for framework in active_frameworks:
            skill_dir = output_root.joinpath(*_FRAMEWORK_ROOTS[framework], exported_name)
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
            (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
            (skill_dir / "agents" / "openai.yaml").write_text(openai_yaml, encoding="utf-8")
            exported.output_paths[framework] = str(skill_dir)

        export_result.skills.append(exported)

    return export_result


def export_packs_to_agent_skills(
    specs: Sequence[str],
    *,
    output_dir: str,
    frameworks: Optional[Sequence[str]] = None,
) -> List[PackExportResult]:
    """Export multiple packs to Agent Skills directory layouts."""

    return [
        export_pack_to_agent_skills(spec, output_dir=output_dir, frameworks=frameworks)
        for spec in specs
    ]
