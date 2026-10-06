"""Tests for scene/snippet templates and the offline manimlib API index."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from manim_editor.scene_runner import run_trace
from manim_editor.templates import (
    SCENE_TEMPLATES,
    SNIPPET_TEMPLATES,
    Template,
    TemplateError,
    find_template,
    render_scene_template,
    snippet_titles,
)

INDENT = "        "


class TestSceneTemplates:
    def test_registry_is_wellformed(self) -> None:
        keys = [template.key for template in SCENE_TEMPLATES]
        assert len(keys) == len(set(keys))
        assert all(template.is_scene for template in SCENE_TEMPLATES)
        assert all(template.title and template.description for template in SCENE_TEMPLATES)

    @pytest.mark.parametrize("template", SCENE_TEMPLATES, ids=lambda t: t.key)
    def test_renders_and_compiles(self, template: Template) -> None:
        code = template.render("DemoScene")
        assert "class DemoScene(" in code
        compile(code, f"{template.key}.py", "exec")

    def test_invalid_scene_name_rejected(self) -> None:
        with pytest.raises(TemplateError):
            render_scene_template("blank", "not a name")

    def test_unknown_template(self) -> None:
        with pytest.raises(TemplateError, match="unknown template"):
            find_template("does_not_exist")

    def test_templates_use_manimgl_naming(self) -> None:
        for template in SCENE_TEMPLATES:
            assert "self.play(Create(" not in template.code

    @pytest.mark.manim
    @pytest.mark.parametrize("template", SCENE_TEMPLATES, ids=lambda t: t.key)
    def test_template_traces(self, template: Template, tmp_path: Path) -> None:
        target = tmp_path / f"{template.key}.py"
        target.write_text(template.render("TemplateSmoke"), encoding="utf-8")
        result = run_trace(target, resolution=(320, 180))
        assert result.scene_name == "TemplateSmoke"
        assert len(result.snapshot.blocks) >= 1


class TestSnippetTemplates:
    def test_registry_is_wellformed(self) -> None:
        keys = [template.key for template in SNIPPET_TEMPLATES]
        assert len(keys) == len(set(keys))
        assert all(not template.is_scene for template in SNIPPET_TEMPLATES)

    @pytest.mark.parametrize("template", SNIPPET_TEMPLATES, ids=lambda t: t.key)
    def test_snippets_compile_inside_construct(self, template: Template) -> None:
        body = textwrap.indent(template.code.rstrip("\n"), INDENT)
        source = (
            f"from manimlib import *\n\n\nclass Demo(Scene):\n    def construct(self):\n{body}\n"
        )
        compile(source, f"{template.key}.py", "exec")

    def test_snippet_render_is_identity(self) -> None:
        template = find_template("wait", kind="snippet")
        assert template.render() == "self.wait(1.0)\n"

    def test_snippet_titles_match_registry(self) -> None:
        assert snippet_titles() == [(t.key, t.title) for t in SNIPPET_TEMPLATES]

    def test_kind_filter(self) -> None:
        with pytest.raises(TemplateError):
            find_template("blank", kind="snippet")
        with pytest.raises(TemplateError):
            find_template("wait", kind="scene")
