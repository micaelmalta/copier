import platform
import stat
import sys
from contextlib import nullcontext as does_not_raise
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, ContextManager, List

import pytest
import yaml
from plumbum import local
from plumbum.cmd import git
from prompt_toolkit.validation import ValidationError

import copier
from copier import run_copy
from copier.types import AnyByStrDict

from .helpers import (
    BRACKET_ENVOPS,
    BRACKET_ENVOPS_JSON,
    PROJECT_TEMPLATE,
    assert_file,
    build_file_tree,
    filecmp,
    git_save,
    render,
)


def test_project_not_found(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        copier.run_copy("foobar", tmp_path)

    with pytest.raises(ValueError):
        copier.run_copy(__file__, tmp_path)


def test_copy_with_non_version_tags(tmp_path_factory: pytest.TempPathFactory) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / ".copier-answers.yml.jinja"): (
                """\
                # Changes here will be overwritten by Copier
                {{ _copier_answers|to_nice_yaml }}
                """
            ),
            (src / "copier.yaml"): (
                """\
                _tasks:
                    - cat v1.txt
                """
            ),
            (src / "v1.txt"): "file only in v1",
        }
    )
    with local.cwd(src):
        git("init")
        git("add", ".")
        git("commit", "-m1")
        git("tag", "test_tag.post23+deadbeef")

    run_copy(
        str(src),
        dst,
        defaults=True,
        overwrite=True,
        unsafe=True,
        vcs_ref="HEAD",
    )


@pytest.mark.parametrize(
    "tag2, use_prereleases, expected_version",
    [
        ("1.2.3.post1", True, "2"),
        ("1.2.4.dev1", True, "2"),
        ("1.2.3.post1", False, "2"),
        ("1.2.4.dev1", False, "1"),
    ],
)
@pytest.mark.parametrize("prefix", ["", "v"])
def test_tag_autodetect_v_prefix_optional(
    tmp_path_factory: pytest.TempPathFactory,
    tag2: str,
    use_prereleases: bool,
    expected_version,
    prefix: str,
) -> None:
    """Tags with and without `v` prefix should work the same."""
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "version.txt"): "1",
            (
                src / "{{_copier_conf.answers_file}}.jinja"
            ): "{{ _copier_answers|to_nice_yaml -}}",
        }
    )
    # One stable tag
    git_save(src, tag=f"{prefix}1.2.3")
    # One pre or post release tag
    (src / "version.txt").write_text("2")
    git_save(src, tag=f"{prefix}{tag2}")
    # One untagged change
    (src / "version.txt").write_text("3")
    git_save(src)
    # Copy, leaving latest tag auto-detection
    copier.run_copy(
        str(src),
        dst,
        use_prereleases=use_prereleases,
        defaults=True,
        unsafe=True,
    )
    # Check it works as expected
    assert (dst / "version.txt").read_text() == expected_version


def test_copy_with_non_version_tags_and_vcs_ref(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / ".copier-answers.yml.jinja"): (
                """\
                # Changes here will be overwritten by Copier
                {{ _copier_answers|to_nice_yaml }}
                """
            ),
            (src / "copier.yaml"): (
                """\
                _tasks:
                    - cat v1.txt
                """
            ),
            (src / "v1.txt"): "file only in v1",
        }
    )
    with local.cwd(src):
        git("init")
        git("add", ".")
        git("commit", "-m1")
        git("tag", "test_tag.post23+deadbeef")

    run_copy(
        str(src),
        dst,
        defaults=True,
        overwrite=True,
        unsafe=True,
        vcs_ref="test_tag.post23+deadbeef",
    )


def test_copy_with_vcs_ref_branch(tmp_path_factory: pytest.TempPathFactory) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    with local.cwd(src):
        git("init")
        main_branch = git("branch", "--show-current").strip()
        git("add", ".")
        git("commit", "-m1", "--allow-empty")
        git("checkout", "-b", "branch")
        git("commit", "-m2", "--allow-empty")
        git("checkout", main_branch)
    run_copy(str(src), dst, vcs_ref="branch")


def test_copy(tmp_path: Path) -> None:
    render(tmp_path)

    generated = (tmp_path / "pyproject.toml").read_text()
    control = (Path(__file__).parent / "reference_files" / "pyproject.toml").read_text()
    assert generated == control

    assert_file(tmp_path, "doc", "mañana.txt")
    assert_file(tmp_path, "doc", "images", "nslogo.gif")

    p1 = tmp_path / "awesome" / "hello.txt"
    p2 = PROJECT_TEMPLATE / "[[ myvar ]]" / "hello.txt"
    assert filecmp.cmp(p1, p2)

    assert (tmp_path / "README.txt").read_text() == "This is the README for Copier.\n"

    p1 = tmp_path / "awesome.txt"
    p2 = PROJECT_TEMPLATE / "[[ myvar ]].txt"
    assert filecmp.cmp(p1, p2)

    assert not (tmp_path / "[% if not py3 %]py2_only.py[% endif %]").exists()
    assert not (tmp_path / "[% if py3 %]py3_only.py[% endif %]").exists()
    assert not (tmp_path / "py2_only.py").exists()
    assert (tmp_path / "py3_only.py").exists()
    assert not (
        tmp_path / "[% if not py3 %]py2_folder[% endif %]" / "thing.py"
    ).exists()
    assert not (tmp_path / "[% if py3 %]py3_folder[% endif %]" / "thing.py").exists()
    assert not (tmp_path / "py2_folder" / "thing.py").exists()
    assert (tmp_path / "py3_folder" / "thing.py").exists()

    assert (tmp_path / "aaaa.txt").exists()


@pytest.mark.impure
def test_copy_repo(tmp_path: Path) -> None:
    copier.run_copy(
        "gh:copier-org/copier.git",
        tmp_path,
        vcs_ref="HEAD",
        quiet=True,
        exclude=["*", "!README.*"],
    )
    assert (tmp_path / "README.md").exists()


def test_default_exclude(tmp_path: Path) -> None:
    render(tmp_path)
    assert not (tmp_path / ".svn").exists()


def test_include_file(tmp_path: Path) -> None:
    render(tmp_path, exclude=["!.svn"])
    assert_file(tmp_path, ".svn")


def test_include_pattern(tmp_path: Path) -> None:
    render(tmp_path, exclude=["!.*"])
    assert (tmp_path / ".svn").exists()


@pytest.mark.xfail(
    condition=platform.system() == "Darwin",
    reason="Mac claims to use UTF-8 filesystem, but behaves differently.",
    strict=True,
)
def test_exclude_file(tmp_path: Path) -> None:
    print(f"Filesystem encoding is {sys.getfilesystemencoding()}")
    # This file name is b"man\xcc\x83ana.txt".decode()
    render(tmp_path, exclude=["mañana.txt"])
    assert not (tmp_path / "doc" / "mañana.txt").exists()
    # This file name is b"ma\xc3\xb1ana.txt".decode()
    assert (tmp_path / "doc" / "mañana.txt").exists()
    assert (tmp_path / "doc" / "manana.txt").exists()


def test_exclude_extends(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Exclude argument extends the original exclusions instead of replacing them."""
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree({src / "test.txt": "Test text", src / "test.json": '"test json"'})
    # Convert to git repo
    with local.cwd(src):
        git("init")
        git("add", ".")
        git("commit", "-m", "hello world")
    copier.run_copy(str(src), dst, exclude=["*.txt"])
    assert (dst / "test.json").is_file()
    assert not (dst / "test.txt").exists()
    # .git exists in src, but not in dst because it is excluded by default
    assert not (dst / ".git").exists()


def test_exclude_replaces(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Exclude in copier.yml replaces default values."""
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            src / "test.txt": "Test text",
            src / "test.json": '"test json"',
            src / "test.yaml": '"test yaml"',
            src / "copier.yaml.jinja": "purpose: template inception",
            src / "copier.yml": "_exclude: ['*.json']",
        }
    )
    copier.run_copy(str(src), dst, exclude=["*.txt"])
    assert (dst / "test.yaml").is_file()
    assert not (dst / "test.txt").exists()
    assert not (dst / "test.json").exists()
    assert (dst / "copier.yml").exists()
    assert (dst / "copier.yaml").is_file()


def test_skip_if_exists(tmp_path: Path) -> None:
    copier.run_copy(str(Path("tests", "demo_skip_dst")), tmp_path)
    copier.run_copy(
        "tests/demo_skip_src",
        tmp_path,
        skip_if_exists=["b.noeof.txt", "meh/c.noeof.txt"],
        defaults=True,
        overwrite=True,
    )

    assert (tmp_path / "a.noeof.txt").read_text() == "SKIPPED"
    assert (tmp_path / "b.noeof.txt").read_text() == "SKIPPED"
    assert (tmp_path / "meh" / "c.noeof.txt").read_text() == "SKIPPED"


def test_skip_if_exists_rendered_patterns(tmp_path: Path) -> None:
    copier.run_copy("tests/demo_skip_dst", tmp_path)
    copier.run_copy(
        "tests/demo_skip_src",
        tmp_path,
        data={"name": "meh"},
        skip_if_exists=["{{ name }}/c.noeof.txt"],
        defaults=True,
        overwrite=True,
    )
    assert (tmp_path / "a.noeof.txt").read_text() == "SKIPPED"
    assert (tmp_path / "b.noeof.txt").read_text() == "OVERWRITTEN"
    assert (tmp_path / "meh" / "c.noeof.txt").read_text() == "SKIPPED"


def test_skip_option(tmp_path: Path) -> None:
    render(tmp_path)
    path = tmp_path / "pyproject.toml"
    content = "lorem ipsum"
    path.write_text(content)
    render(tmp_path, skip_if_exists=["**"])
    assert path.read_text() == content


def test_force_option(tmp_path: Path) -> None:
    render(tmp_path)
    path = tmp_path / "pyproject.toml"
    content = "lorem ipsum"
    path.write_text(content)
    render(tmp_path, defaults=True, overwrite=True)
    assert path.read_text() != content


def test_pretend_option(tmp_path: Path) -> None:
    render(tmp_path, pretend=True)
    assert not (tmp_path / "doc").exists()
    assert not (tmp_path / "config.py").exists()
    assert not (tmp_path / "pyproject.toml").exists()


@pytest.mark.parametrize("generate", [True, False])
def test_empty_dir(tmp_path_factory: pytest.TempPathFactory, generate: bool) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yaml"): (
                f"""\
                _subdirectory: tpl
                _templates_suffix: .jinja
                _envops: {BRACKET_ENVOPS_JSON}
                do_it:
                    type: bool
                """
            ),
            (src / "tpl" / "[% if do_it %]one_dir[% endif %]" / "one.txt.jinja"): (
                "[[ do_it ]]"
            ),
            (src / "tpl" / "two.txt"): "[[ do_it ]]",
            (src / "tpl" / "[% if do_it %]three.txt[% endif %].jinja"): "[[ do_it ]]",
            (src / "tpl" / "[% if do_it %]four.txt[% endif %].jinja"): Path(
                "[% if do_it %]three.txt[% endif %].jinja"
            ),
            (src / "tpl" / "five" / "[% if do_it %]six.txt[% endif %].jinja"): (
                "[[ do_it ]]"
            ),
        },
    )
    copier.run_copy(str(src), dst, {"do_it": generate}, defaults=True, overwrite=True)
    assert (dst / "five").is_dir()
    assert (dst / "two.txt").read_text() == "[[ do_it ]]"
    assert (dst / "one_dir").exists() == generate
    assert (dst / "three.txt").exists() == generate
    assert (dst / "four.txt").exists() == generate
    assert (dst / "one_dir").is_dir() == generate
    assert (dst / "one_dir" / "one.txt").is_file() == generate
    if generate:
        assert (dst / "one_dir" / "one.txt").read_text() == repr(generate)
        assert (dst / "three.txt").read_text() == repr(generate)
        assert not (dst / "four.txt").is_symlink()
        assert (dst / "four.txt").read_text() == repr(generate)
        assert (dst / "five" / "six.txt").read_text() == repr(generate)


@pytest.mark.skipif(
    condition=platform.system() == "Windows",
    reason="Windows does not have UNIX-like permissions",
)
@pytest.mark.parametrize(
    "permissions",
    (
        stat.S_IRUSR,
        stat.S_IRWXU,
        stat.S_IRWXU | stat.S_IRWXG,
        stat.S_IRWXU | stat.S_IRWXO,
        stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO,
    ),
)
def test_preserved_permissions(
    tmp_path_factory: pytest.TempPathFactory, permissions: int
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    src_file = src / "the_file"
    src_file.write_text("content")
    src_file.chmod(permissions)
    copier.run_copy(str(src), dst, defaults=True, overwrite=True)
    assert ((dst / "the_file").stat().st_mode & permissions) == permissions


def test_commit_hash(tmp_path_factory: pytest.TempPathFactory) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            src / "commit.jinja": "{{_copier_conf.vcs_ref_hash}}",
            src / "tag.jinja": "{{_copier_answers._commit}}",
        }
    )
    with local.cwd(src):
        git("init")
        git("add", "-A")
        git("commit", "-m1")
        git("tag", "1.0")
        commit = git("rev-parse", "HEAD").strip()
    copier.run_copy(str(src), dst)
    assert (dst / "tag").read_text() == "1.0"
    assert (dst / "commit").read_text() == commit


def test_value_with_forward_slash(tmp_path_factory: pytest.TempPathFactory) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "{{ filename.replace('.', _copier_conf.sep) }}.txt"): (
                "This is template."
            ),
        }
    )
    copier.run_copy(str(src), dst, data={"filename": "a.b.c"})
    assert (dst / "a" / "b" / "c.txt").read_text() == "This is template."


@pytest.mark.parametrize(
    "spec, value, expected",
    [
        ({"type": "int"}, "no-int", pytest.raises(ValueError)),
        ({"type": "int"}, "0", does_not_raise()),
        (
            {
                "type": "int",
                "validator": "[% if q <= 0 %]must be positive[% endif %]",
            },
            "0",
            pytest.raises(ValidationError),
        ),
        (
            {
                "type": "int",
                "validator": "[% if q <= 0 %]must be positive[% endif %]",
            },
            "1",
            does_not_raise(),
        ),
        ({"type": "str"}, "string", does_not_raise()),
        ({"type": "str"}, b"bytes", does_not_raise()),
        ({"type": "str"}, bytearray("abc", "utf-8"), does_not_raise()),
        ({"type": "str"}, 1, does_not_raise()),
        ({"type": "str"}, 1.1, does_not_raise()),
        ({"type": "str"}, True, does_not_raise()),
        ({"type": "str"}, False, does_not_raise()),
        ({"type": "str"}, Decimal(1.1), does_not_raise()),
        ({"type": "str"}, Enum("A", ["a", "b"], type=str).a, does_not_raise()),  # type: ignore
        ({"type": "str"}, Enum("A", ["a", "b"]).a, pytest.raises(ValueError)),  # type: ignore
        ({"type": "str"}, object(), pytest.raises(ValueError)),
        ({"type": "str"}, {}, pytest.raises(ValueError)),
        ({"type": "str"}, [], pytest.raises(ValueError)),
        (
            {
                "type": "str",
                "validator": "[% if q|length < 3 %]too short[% endif %]",
            },
            "ab",
            pytest.raises(ValidationError),
        ),
        (
            {
                "type": "str",
                "validator": "[% if q|length < 3 %]too short[% endif %]",
            },
            "abc",
            does_not_raise(),
        ),
        ({"type": "bool"}, "true", does_not_raise()),
        ({"type": "bool"}, "false", does_not_raise()),
        (
            {"type": "int", "choices": [1, 2, 3]},
            "1",
            does_not_raise(),
        ),
        (
            {"type": "int", "choices": [1, 2, 3]},
            "4",
            pytest.raises(ValueError),
        ),
        (
            {
                "type": "int",
                "choices": [1, ["2", {"value": None}], 3],
            },
            "2",
            does_not_raise(),
        ),
        (
            {
                "type": "int",
                "choices": [1, ["2", {"value": None, "disabled": ""}], 3],
            },
            "2",
            does_not_raise(),
        ),
        (
            {
                "type": "int",
                "choices": [1, ["2", {"value": None, "validator": "disabled"}], 3],
            },
            "2",
            pytest.raises(ValueError, match="Invalid choice: disabled"),
        ),
        (
            {"type": "int", "choices": {"one": 1, "two": 2, "three": 3}},
            "1",
            does_not_raise(),
        ),
        (
            {"type": "int", "choices": {"one": 1, "two": 2, "three": 3}},
            "4",
            pytest.raises(ValueError),
        ),
        (
            {"type": "int", "choices": {"one": 1, "two": {"value": 2}, "three": 3}},
            "2",
            does_not_raise(),
        ),
        (
            {
                "type": "int",
                "choices": {
                    "one": 1,
                    "two": {"value": 2, "validator": ""},
                    "three": 3,
                },
            },
            "2",
            does_not_raise(),
        ),
        (
            {
                "type": "int",
                "choices": {
                    "one": 1,
                    "two": {"value": 2, "validator": "disabled"},
                    "three": 3,
                },
            },
            "2",
            pytest.raises(ValueError, match="Invalid choice: disabled"),
        ),
        (
            {"type": "str", "choices": {"one": None, "two": None, "three": None}},
            "one",
            does_not_raise(),
        ),
        (
            {"type": "str", "choices": {"one": None, "two": None, "three": None}},
            "four",
            pytest.raises(ValueError),
        ),
        (
            {
                "type": "str",
                "choices": {
                    "banana": {"value": "yellow", "validator": "invalid"},
                    "tshirt": {"value": "yellow", "validator": ""},
                },
            },
            "yellow",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"one": None, "two": 2, "three": "null"}},
            "one",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"one": None, "two": 2, "three": "null"}},
            "2",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"one": None, "two": 2, "three": "null"}},
            "null",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"one": None, "two": 2, "three": "null"}},
            "two",
            pytest.raises(ValueError),
        ),
        (
            {"type": "yaml", "choices": {"one": None, "two": 2, "three": "null"}},
            "three",
            pytest.raises(ValueError),
        ),
        (
            {"type": "yaml", "choices": {"array": "[a, b]"}},
            "[a, b]",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"object": "k: v"}},
            "k: v",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": [{"one": 1, "two": 2}]},
            "one: 1\ntwo: 2",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": {"complex": {"value": {"key": "value"}}}},
            "key: value",
            does_not_raise(),
        ),
        (
            {
                "type": "yaml",
                "choices": {"complex": {"value": {"key": "value"}, "validator": ""}},
            },
            "key: value",
            does_not_raise(),
        ),
        (
            {
                "type": "yaml",
                "choices": {
                    "complex": {"value": {"key": "value"}, "validator": "disabled"}
                },
            },
            "key: value",
            pytest.raises(ValueError, match="Invalid choice: disabled"),
        ),
        (
            {"type": "yaml", "choices": {"complex": {"key": "value"}}},
            "key: value",
            pytest.raises(KeyError, match="Property 'value' is required"),
        ),
        (
            {"type": "yaml", "choices": {"complex": {"value": 1, "validator": {}}}},
            "1",
            pytest.raises(ValueError, match="Property 'validator' must be a string"),
        ),
        (
            {"type": "yaml", "choices": [{"key": "value"}]},
            "key: value",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": [{"value": 1, "validator": {}}]},
            "value: 1\nvalidator: {}",
            does_not_raise(),
        ),
        (
            {"type": "yaml", "choices": [["complex", {"value": {"key": "value"}}]]},
            "key: value",
            does_not_raise(),
        ),
        (
            {
                "type": "yaml",
                "choices": [["complex", {"value": {"key": "value"}, "validator": ""}]],
            },
            "key: value",
            does_not_raise(),
        ),
        (
            {
                "type": "yaml",
                "choices": [
                    ["complex", {"value": {"key": "value"}, "validator": "disabled"}]
                ],
            },
            "key: value",
            pytest.raises(ValueError, match="Invalid choice: disabled"),
        ),
        (
            {"type": "yaml", "choices": [["complex", {"key": "value"}]]},
            "key: value",
            pytest.raises(KeyError, match="Property 'value' is required"),
        ),
        (
            {"type": "yaml", "choices": [["complex", {"value": 1, "validator": {}}]]},
            "1",
            pytest.raises(ValueError, match="Property 'validator' must be a string"),
        ),
    ],
)
def test_validate_init_data(
    tmp_path_factory: pytest.TempPathFactory,
    spec: AnyByStrDict,
    value: str,
    expected: ContextManager[None],
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yml"): yaml.dump(
                {
                    "_envops": BRACKET_ENVOPS,
                    "_templates_suffix": ".jinja",
                    "q": spec,
                }
            ),
        }
    )
    with expected:
        copier.run_copy(str(src), dst, data={"q": value})


@pytest.mark.parametrize("defaults", [False, True])
def test_validate_init_data_with_skipped_question(
    tmp_path_factory: pytest.TempPathFactory, defaults: bool
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yml"): (
                f"""\
                _envops: {BRACKET_ENVOPS_JSON}

                kind:
                    type: str
                    help: What kind do you need?
                    choices:
                        foo: foo
                        bar: bar

                testbar:
                    when: "[[ kind == 'bar' ]]"
                    type: str
                    help: any string bar?

                testfoo:
                    when: "[[ kind == 'foo' ]]"
                    type: str
                    help: any string foo?
                """
            ),
            (src / "result.jinja"): (
                """\
                [[ kind ]]
                [[ testbar ]]
                [[ testfoo ]]
                """
            ),
        }
    )
    copier.run_copy(
        str(src), dst, defaults=defaults, data={"kind": "foo", "testfoo": "helloworld"}
    )
    assert (dst / "result").read_text() == "foo\n\nhelloworld\n"


@pytest.mark.parametrize(
    "type_name",
    ["str", "int", "float", "bool", "json", "yaml"],
)
def test_required_question_without_data(
    tmp_path_factory: pytest.TempPathFactory, type_name: str
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yml"): yaml.safe_dump(
                {
                    "question": {
                        "type": type_name,
                    }
                }
            )
        }
    )
    with pytest.raises(ValueError, match='Question "question" is required'):
        copier.run_copy(str(src), dst, defaults=True)


@pytest.mark.parametrize(
    "type_name, choices",
    [
        ("str", ["one", "two", "three"]),
        ("int", [1, 2, 3]),
        ("float", [1.0, 2.0, 3.0]),
        ("json", ["[1]", "[2]", "[3]"]),
        ("yaml", ["- 1", "- 2", "- 3"]),
    ],
)
def test_required_choice_question_without_data(
    tmp_path_factory: pytest.TempPathFactory, type_name: str, choices: List[Any]
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yml"): yaml.safe_dump(
                {
                    "question": {
                        "type": type_name,
                        "choices": choices,
                    }
                }
            )
        }
    )
    with pytest.raises(ValueError, match='Question "question" is required'):
        copier.run_copy(str(src), dst, defaults=True)


@pytest.mark.parametrize(
    "type_name, default, expected",
    [
        ("str", "string", does_not_raise()),
        ("str", "1.0", does_not_raise()),
        ("str", 1.0, does_not_raise()),
        ("str", None, pytest.raises(TypeError)),
        ("int", 1, does_not_raise()),
        ("int", 1.0, does_not_raise()),
        ("int", "1", does_not_raise()),
        ("int", "1.0", pytest.raises(ValueError)),
        ("int", "no-int", pytest.raises(ValueError)),
        ("int", None, pytest.raises(TypeError)),
        ("int", {}, pytest.raises(TypeError)),
        ("int", [], pytest.raises(TypeError)),
        ("float", 1.1, does_not_raise()),
        ("float", 1, does_not_raise()),
        ("float", "1.1", does_not_raise()),
        ("float", "no-float", pytest.raises(ValueError)),
        ("float", None, pytest.raises(TypeError)),
        ("float", {}, pytest.raises(TypeError)),
        ("float", [], pytest.raises(TypeError)),
        ("bool", True, does_not_raise()),
        ("bool", False, does_not_raise()),
        ("bool", "y", does_not_raise()),
        ("bool", "n", does_not_raise()),
        ("bool", None, pytest.raises(TypeError)),
        ("json", '"string"', does_not_raise()),
        ("json", "1", does_not_raise()),
        ("json", 1, does_not_raise()),
        ("json", "1.1", does_not_raise()),
        ("json", 1.1, does_not_raise()),
        ("json", "true", does_not_raise()),
        ("json", True, does_not_raise()),
        ("json", "false", does_not_raise()),
        ("json", False, does_not_raise()),
        ("json", "{}", does_not_raise()),
        ("json", {}, does_not_raise()),
        ("json", "[]", does_not_raise()),
        ("json", [], does_not_raise()),
        ("json", "null", does_not_raise()),
        ("json", None, does_not_raise()),
        ("yaml", '"string"', does_not_raise()),
        ("yaml", "string", does_not_raise()),
        ("yaml", "1", does_not_raise()),
        ("yaml", 1, does_not_raise()),
        ("yaml", "1.1", does_not_raise()),
        ("yaml", 1.1, does_not_raise()),
        ("yaml", "true", does_not_raise()),
        ("yaml", True, does_not_raise()),
        ("yaml", "false", does_not_raise()),
        ("yaml", False, does_not_raise()),
        ("yaml", "{}", does_not_raise()),
        ("yaml", {}, does_not_raise()),
        ("yaml", "[]", does_not_raise()),
        ("yaml", [], does_not_raise()),
        ("yaml", "null", does_not_raise()),
        ("yaml", None, does_not_raise()),
    ],
)
def test_validate_default_value(
    tmp_path_factory: pytest.TempPathFactory,
    type_name: str,
    default: Any,
    expected: ContextManager[None],
) -> None:
    src, dst = map(tmp_path_factory.mktemp, ("src", "dst"))
    build_file_tree(
        {
            (src / "copier.yml"): yaml.dump(
                {
                    "q": {
                        "type": type_name,
                        "default": default,
                    }
                }
            )
        }
    )
    with expected:
        copier.run_copy(str(src), dst, defaults=True)
