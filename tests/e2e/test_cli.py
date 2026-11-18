"""E2E tests for the CLI (fhir_converter/__main__.py).

Covers: main, parse_args, get_renderer, get_user_defined_environment, render,
render_to_dir, render_files_to_dir, is_ccda_file, get_onerror.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import ROOT

pytestmark = pytest.mark.e2e


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "fhir_converter", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_cli_ccda_single_file(tmp_path, samples):
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", tmp_path,
                "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 0, r.stderr
    assert "RENDER SUCCESS" in r.stdout
    out = json.loads((tmp_path / "CCD.json").read_text(encoding="utf-8"))
    assert out["resourceType"] == "Bundle"


def test_cli_stu3_single_file(tmp_path, samples):
    r = run_cli("--from-file", samples / "stu3" / "Patient.json", "--to-dir", tmp_path,
                "--template-name", "Patient", "--source-format", "STU3")
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "Patient.json").read_text(encoding="utf-8"))["resourceType"] == "Patient"


def test_cli_from_dir_continue_on_error(tmp_path):
    src = ROOT / "tests" / "data" / "nested"
    r = run_cli("--from-dir", src, "--to-dir", tmp_path, "--template-name", "CCD",
                "--source-format", "CCDA", "--continue_on_error", "--flatten_to_dir")
    assert r.returncode == 0, r.stderr
    rendered = sorted(p.name for p in tmp_path.glob("*.json"))
    assert {"CCD.json", "sample.json"} <= set(rendered)


def test_cli_from_dir_fails_fast_without_continue(tmp_path):
    src = ROOT / "tests" / "data" / "nested"
    r = run_cli("--from-dir", src, "--to-dir", tmp_path / "out", "--template-name", "CCD",
                "--source-format", "CCDA")
    assert r.returncode == 1
    assert "RENDER FAILURE" in r.stdout


def test_cli_requires_input(tmp_path):
    r = run_cli("--to-dir", tmp_path, "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode != 0
    assert "--from-file or --from-dir" in r.stderr


def test_cli_rejects_unknown_source_format(tmp_path, samples):
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", tmp_path,
                "--template-name", "CCD", "--source-format", "HL7V2")
    assert r.returncode != 0


def test_cli_from_dir_keeps_nested_structure(tmp_path):
    """Without --flatten_to_dir, render_files_to_dir mirrors subdirectories and
    del_empty_dirs_quietly removes dirs left empty by failed files."""
    src = ROOT / "tests" / "data" / "nested"
    r = run_cli("--from-dir", src, "--to-dir", tmp_path, "--template-name", "CCD",
                "--source-format", "CCDA", "--continue_on_error")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "ccda" / "CCD.json").is_file()
    assert (tmp_path / "ccda" / "sample.json").is_file()
    assert not (tmp_path / "bad_data").exists()
    assert "Traceback" in r.stderr  # get_onerror(continue_on_error) prints, does not raise


def test_cli_from_dir_filters_non_ccda_files(tmp_path, samples):
    """is_ccda_file: only .ccda and .xml inputs are rendered."""
    src = tmp_path / "in"
    src.mkdir()
    ccd = (samples / "ccda" / "CCD.ccda").read_bytes()
    (src / "a.ccda").write_bytes(ccd)
    (src / "b.xml").write_bytes(ccd)
    (src / "ignore.txt").write_bytes((samples / "ccda" / "ignore.txt").read_bytes())
    out = tmp_path / "out"
    r = run_cli("--from-dir", src, "--to-dir", out, "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["a.json", "b.json"]


def test_cli_creates_missing_to_dir_and_keeps_output(tmp_path, samples):
    out = tmp_path / "new_dir"  # mkdir in render() does not create missing parents
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", out,
                "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 0, r.stderr
    assert (out / "CCD.json").is_file()


def test_cli_failure_removes_created_to_dir(tmp_path):
    out = tmp_path / "out"
    r = run_cli("--from-file", ROOT / "tests" / "data" / "nested" / "bad_data" / "empty.ccda",
                "--to-dir", out, "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 1
    assert "RENDER FAILURE" in r.stdout
    assert not out.exists()


def test_cli_user_template_dir_overrides(tmp_path, samples):
    """get_user_defined_environment + make_environment(additional_loaders) with a custom template."""
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "Custom.liquid").write_text(
        '{"resourceType": "{{ msg.resourceType }}", "id": "{{ msg.id }}", '
        '"meta": {"tag": [{"code": "custom-template"}]}}',
        encoding="utf-8",
    )
    out = tmp_path / "out"
    r = run_cli("--from-file", samples / "stu3" / "Patient.json", "--to-dir", out,
                "--template-dir", tpl, "--template-name", "Custom", "--source-format", "STU3")
    assert r.returncode == 0, r.stderr
    src = json.loads((samples / "stu3" / "Patient.json").read_text(encoding="utf-8"))
    result = json.loads((out / "Patient.json").read_text(encoding="utf-8"))
    assert result["meta"]["tag"][0]["code"] == "custom-template"
    assert result["id"] == src["id"]


def test_cli_user_template_dir_falls_back_to_defaults(tmp_path, samples):
    """A template missing from --template-dir is resolved by the default package loader."""
    tpl = tmp_path / "empty_templates"
    tpl.mkdir()
    out = tmp_path / "out"
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", out,
                "--template-dir", tpl, "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 0, r.stderr
    assert json.loads((out / "CCD.json").read_text(encoding="utf-8"))["resourceType"] == "Bundle"


@pytest.mark.xfail(
    strict=True,
    reason="APP BUG: __main__.render always passes path_filter=is_ccda_file, so "
           "--from-dir with --source-format STU3 skips every .json file and still reports success",
)
def test_cli_stu3_from_dir_renders_json_files(tmp_path, samples):
    out = tmp_path / "out"
    r = run_cli("--from-dir", samples / "stu3", "--to-dir", out,
                "--template-name", "Patient", "--source-format", "STU3", "--continue_on_error")
    assert r.returncode == 0, r.stderr
    assert (out / "Patient.json").is_file()


def test_cli_console_script_entrypoint(tmp_path, samples):
    """entrypoint(): the installed `fhir_converter_cli` script (pyproject [tool.poetry.scripts])."""
    scripts = Path(sys.executable).parent
    exe = next((p for p in (scripts / "fhir_converter_cli.exe", scripts / "fhir_converter_cli") if p.exists()), None)
    if exe is None:
        pytest.skip("fhir_converter_cli console script not installed; run `pip install -e .`")
    r = subprocess.run(
        [str(exe), "--from-file", str(samples / "stu3" / "Condition.json"), "--to-dir", str(tmp_path),
         "--template-name", "Condition", "--source-format", "STU3"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, r.stderr
    assert "RENDER SUCCESS" in r.stdout
    assert json.loads((tmp_path / "Condition.json").read_text(encoding="utf-8"))["resourceType"] == "Condition"


def test_cli_help_describes_formats():
    """get_argparser: description and options are exposed via --help."""
    r = run_cli("--help")
    assert r.returncode == 0
    assert "Render data formats to FHIR v4" in r.stdout
    for opt in ["--from-file", "--from-dir", "--to-dir", "--template-dir",
                "--template-name", "--source-format", "--continue_on_error", "--flatten_to_dir"]:
        assert opt in r.stdout


def test_cli_requires_template_name(tmp_path, samples):
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", tmp_path,
                "--source-format", "CCDA")
    assert r.returncode == 2
    assert "--template-name" in r.stderr


def test_cli_relative_from_file_is_resolved(tmp_path):
    """absolute_path: a path relative to the working directory is accepted."""
    r = run_cli("--from-file", "data/sample/stu3/Observation.json", "--to-dir", tmp_path,
                "--template-name", "Observation", "--source-format", "STU3")
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "Observation.json").read_text(encoding="utf-8"))["resourceType"] == "Observation"


def test_cli_unknown_template_fails_and_leaves_no_output(tmp_path, samples):
    """render_to_dir deletes the partial output file and fail() re-raises."""
    r = run_cli("--from-file", samples / "ccda" / "CCD.ccda", "--to-dir", tmp_path,
                "--template-name", "NoSuchTemplate", "--source-format", "CCDA")
    assert r.returncode == 1
    assert "RENDER FAILURE" in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_cli_missing_input_file_fails(tmp_path):
    r = run_cli("--from-file", tmp_path / "does_not_exist.ccda", "--to-dir", tmp_path,
                "--template-name", "CCD", "--source-format", "CCDA")
    assert r.returncode == 1
    assert "RENDER FAILURE" in r.stdout
