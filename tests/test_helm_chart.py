"""Tests for the pipeline-status Helm chart artefacts.

These tests parse `Chart.yaml` and `values.yaml` with a hand-rolled,
stdlib-only YAML reader (`_parse_simple_yaml`) so the test suite stays
dependency-free per the v1 / v5 ADRs ("no `import yaml`"). All other
chart files (templates, .helmignore, READMEs) are treated as text and
inspected via plain substring / line scans.

What `_parse_simple_yaml` supports:
    - mappings (`key: value`) with arbitrary indentation depth
    - block lists (`- item`)
    - simple flow lists (`[a, b]`)
    - bare scalars (`true`, `false`, integers, floats, plain strings)
    - quoted strings (single or double quoted)
    - full-line `#` comments
    - empty `{}` / `[]` for empty mappings / lists

What it does NOT support (and does not need to for our chart files):
    - YAML anchors / aliases / merge keys
    - multi-line scalars (`|`, `>`)
    - mid-line comments after non-string scalars
    - nested flow collections (`[[a, b], [c, d]]`)
"""

from __future__ import annotations

import unittest
from pathlib import Path


# --------------------------------------------------------------------------- #
# Repo-root discovery + cached file paths                                     #
# --------------------------------------------------------------------------- #

def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml).")


REPO_ROOT = _repo_root()
CHART_DIR = REPO_ROOT / "charts" / "pipeline-status"
CHART_YAML = CHART_DIR / "Chart.yaml"
VALUES_YAML = CHART_DIR / "values.yaml"
HELMIGNORE = CHART_DIR / ".helmignore"
CHART_README = CHART_DIR / "README.md"
ROOT_README = REPO_ROOT / "README.md"
TEMPLATES_DIR = CHART_DIR / "templates"
CRONJOB_TPL = TEMPLATES_DIR / "cronjob.yaml"
SA_TPL = TEMPLATES_DIR / "serviceaccount.yaml"
HELPERS_TPL = TEMPLATES_DIR / "_helpers.tpl"
NOTES_TXT = TEMPLATES_DIR / "NOTES.txt"


# --------------------------------------------------------------------------- #
# Tiny stdlib-only YAML reader                                                #
# --------------------------------------------------------------------------- #

def _coerce_scalar(raw: str):
    """Coerce a stripped scalar token to bool / int / float / str / None.

    Quoted strings (`'foo'` or `"foo"`) are returned unquoted as `str`.
    Bare `true`/`false`/`null`/`~` map to Python `True`/`False`/`None`.
    Numeric-looking tokens map to `int` or `float`. Everything else is a
    bare string.
    """
    if raw == "" or raw == "~" or raw.lower() == "null":
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    # int
    try:
        if raw.startswith(("-", "+")):
            int(raw[1:])
        else:
            int(raw)
        return int(raw)
    except ValueError:
        pass
    # float
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_flow_list(text: str):
    """Parse a single-level flow list `[a, b, "c"]`. Empty `[]` -> []."""
    inner = text.strip()
    assert inner.startswith("[") and inner.endswith("]")
    inner = inner[1:-1].strip()
    if not inner:
        return []
    # Naive split on commas; chart values never use nested flow collections.
    return [_coerce_scalar(tok.strip()) for tok in inner.split(",")]


def _parse_simple_yaml(text: str):
    """Parse a subset of YAML sufficient for Chart.yaml + values.yaml.

    Returns a nested dict / list / scalar structure. Indentation is tracked
    via a stack of (indent, container) frames. The parser is intentionally
    forgiving — it raises only on truly malformed input.
    """
    root: dict = {}
    # Each frame: (indent_int, container_obj, container_kind)
    # container_kind in {"dict", "list"}
    stack: list = [(-1, root, "dict")]

    raw_lines = text.splitlines()
    # Pre-process: drop comment-only lines and blank lines.
    lines = []
    for ln in raw_lines:
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(ln)

    i = 0
    while i < len(lines):
        line = lines[i]
        # indent = number of leading spaces
        indent = len(line) - len(line.lstrip(" "))
        content = line[indent:]

        # Pop frames whose indent is >= current indent (we go back up).
        # For list items the rule is the same; the `- ` prefix sits at the
        # same column as a sibling key.
        while stack and indent <= stack[-1][0] and stack[-1][0] != -1:
            stack.pop()

        parent_indent, parent, parent_kind = stack[-1]

        if content.startswith("- "):
            # List item.
            item_text = content[2:].strip()
            if parent_kind != "list":
                raise ValueError(f"List item with non-list parent: {line!r}")
            if item_text == "":
                # An empty `-` introduces a nested dict/list — push a new dict.
                new_dict: dict = {}
                parent.append(new_dict)
                stack.append((indent, new_dict, "dict"))
            elif ":" in item_text and not (item_text.startswith(("'", '"'))):
                # Inline `- key: value` — start a dict, set the first key.
                new_dict = {}
                parent.append(new_dict)
                stack.append((indent, new_dict, "dict"))
                key, _, val = item_text.partition(":")
                val = val.strip()
                if val == "":
                    # Nested structure follows on next line.
                    placeholder: dict = {}
                    new_dict[key.strip()] = placeholder
                    # Children indent further than the `-`.
                    stack.append((indent + 2, placeholder, "dict"))
                else:
                    new_dict[key.strip()] = _coerce_scalar(val)
            else:
                parent.append(_coerce_scalar(item_text))
        elif ":" in content:
            key, _, rest = content.partition(":")
            key = key.strip()
            rest = rest.strip()
            if parent_kind != "dict":
                raise ValueError(f"Mapping entry with non-dict parent: {line!r}")
            if rest == "":
                # Children follow on next line — could be dict or list.
                # Peek ahead to decide.
                child_kind = "dict"
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    nxt_content = nxt[nxt_indent:]
                    if nxt_indent > indent and nxt_content.startswith("- "):
                        child_kind = "list"
                if child_kind == "list":
                    new_list: list = []
                    parent[key] = new_list
                    stack.append((indent, new_list, "list"))
                else:
                    new_dict = {}
                    parent[key] = new_dict
                    stack.append((indent, new_dict, "dict"))
            elif rest == "{}":
                parent[key] = {}
            elif rest == "[]":
                parent[key] = []
            elif rest.startswith("["):
                parent[key] = _parse_flow_list(rest)
            else:
                parent[key] = _coerce_scalar(rest)
        else:
            raise ValueError(f"Unparseable line: {line!r}")
        i += 1

    return root


# --------------------------------------------------------------------------- #
# Parser self-tests                                                           #
# --------------------------------------------------------------------------- #

class ParseSimpleYamlSelfTest(unittest.TestCase):
    """Confidence checks for the hand-rolled YAML reader."""

    def test_parses_basic_mapping(self):
        out = _parse_simple_yaml("a: 1\nb: hello\n")
        self.assertEqual(out, {"a": 1, "b": "hello"})

    def test_parses_quoted_strings(self):
        out = _parse_simple_yaml('a: "*/5 * * * *"\nb: \'x\'\n')
        self.assertEqual(out, {"a": "*/5 * * * *", "b": "x"})

    def test_parses_bool_and_empty_string(self):
        out = _parse_simple_yaml('a: true\nb: false\nc: ""\n')
        self.assertEqual(out, {"a": True, "b": False, "c": ""})

    def test_parses_nested_mapping(self):
        text = "outer:\n  inner: 7\n  other: ok\n"
        self.assertEqual(_parse_simple_yaml(text), {"outer": {"inner": 7, "other": "ok"}})

    def test_parses_block_list(self):
        text = "items:\n  - a\n  - b\n  - c\n"
        self.assertEqual(_parse_simple_yaml(text), {"items": ["a", "b", "c"]})

    def test_parses_flow_list_and_empties(self):
        text = "flow: [1, 2, 3]\nempty_map: {}\nempty_list: []\n"
        self.assertEqual(
            _parse_simple_yaml(text),
            {"flow": [1, 2, 3], "empty_map": {}, "empty_list": []},
        )

    def test_ignores_comment_and_blank_lines(self):
        text = "# comment\n\na: 1\n# trailing\n"
        self.assertEqual(_parse_simple_yaml(text), {"a": 1})


# --------------------------------------------------------------------------- #
# Cached parsed artefacts                                                     #
# --------------------------------------------------------------------------- #

def _load_chart_yaml():
    return _parse_simple_yaml(CHART_YAML.read_text(encoding="utf-8"))


def _load_values_yaml():
    return _parse_simple_yaml(VALUES_YAML.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Chart.yaml                                                                  #
# --------------------------------------------------------------------------- #

class ChartYamlTests(unittest.TestCase):

    def setUp(self):
        self.chart = _load_chart_yaml()

    def test_chart_yaml_exists(self):
        self.assertTrue(CHART_YAML.is_file(), f"missing: {CHART_YAML}")

    def test_api_version_is_v2(self):
        self.assertEqual(self.chart.get("apiVersion"), "v2")

    def test_name_is_pipeline_status(self):
        self.assertEqual(self.chart.get("name"), "pipeline-status")

    def test_version_is_0_1_0(self):
        self.assertEqual(self.chart.get("version"), "0.1.0")

    def test_app_version_is_0_1_0(self):
        # appVersion is intentionally string-quoted in the source.
        self.assertEqual(self.chart.get("appVersion"), "0.1.0")

    def test_kube_version_constraint(self):
        self.assertEqual(self.chart.get("kubeVersion"), ">=1.26-0")

    def test_type_is_application(self):
        self.assertEqual(self.chart.get("type"), "application")


# --------------------------------------------------------------------------- #
# values.yaml                                                                 #
# --------------------------------------------------------------------------- #

class ValuesYamlTests(unittest.TestCase):

    def setUp(self):
        self.values = _load_values_yaml()

    def test_image_registry_default(self):
        self.assertEqual(self.values["image"]["registry"], "ghcr.io")

    def test_image_repository_default(self):
        self.assertEqual(self.values["image"]["repository"], "asnapper/master-replicator")

    def test_image_tag_is_empty_string(self):
        # Empty string -> resolves to .Chart.AppVersion at render time.
        self.assertEqual(self.values["image"]["tag"], "")

    def test_image_pull_policy_default(self):
        self.assertEqual(self.values["image"]["pullPolicy"], "IfNotPresent")

    def test_cronjob_schedule_default(self):
        self.assertEqual(self.values["cronjob"]["schedule"], "*/5 * * * *")

    def test_cronjob_concurrency_policy_default(self):
        self.assertEqual(self.values["cronjob"]["concurrencyPolicy"], "Forbid")

    def test_service_account_create_true(self):
        # Parser must normalise YAML `true` to Python True.
        self.assertIs(self.values["serviceAccount"]["create"], True)

    def test_state_volume_enabled_false(self):
        self.assertIs(self.values["stateVolume"]["enabled"], False)

    def test_pod_security_context_run_as_user_65532(self):
        self.assertEqual(self.values["podSecurityContext"]["runAsUser"], 65532)

    def test_container_security_context_run_as_user_65532(self):
        self.assertEqual(self.values["containerSecurityContext"]["runAsUser"], 65532)


# --------------------------------------------------------------------------- #
# Templates                                                                   #
# --------------------------------------------------------------------------- #

class CronJobTemplateTests(unittest.TestCase):

    def setUp(self):
        self.text = CRONJOB_TPL.read_text(encoding="utf-8")

    def test_cronjob_template_exists(self):
        self.assertTrue(CRONJOB_TPL.is_file())

    def test_contains_api_version_batch_v1(self):
        self.assertIn("apiVersion: batch/v1", self.text)

    def test_kind_is_cron_job(self):
        self.assertIn("kind: CronJob", self.text)

    def test_restart_policy_templated_from_values(self):
        self.assertIn("restartPolicy: {{ .Values.cronjob.restartPolicy }}", self.text)

    def test_run_as_user_65532_present(self):
        # Either inline as a literal or via the toYaml of podSecurityContext.
        # We just assert the literal UID appears somewhere in the rendered text.
        self.assertIn("runAsUser: 65532", self.text + VALUES_YAML.read_text(encoding="utf-8"))

    def test_image_uses_named_template(self):
        self.assertIn('image: {{ include "pipeline-status.image" . }}', self.text)


class ServiceAccountTemplateTests(unittest.TestCase):

    def setUp(self):
        self.text = SA_TPL.read_text(encoding="utf-8")

    def test_service_account_template_exists(self):
        self.assertTrue(SA_TPL.is_file())

    def test_template_is_gated_by_service_account_create(self):
        self.assertIn("{{- if .Values.serviceAccount.create -}}", self.text)
        self.assertIn("{{- end }}", self.text)


class HelpersTemplateTests(unittest.TestCase):

    REQUIRED_NAMES = (
        "pipeline-status.name",
        "pipeline-status.fullname",
        "pipeline-status.chart",
        "pipeline-status.labels",
        "pipeline-status.selectorLabels",
        "pipeline-status.serviceAccountName",
        "pipeline-status.image",
    )

    def setUp(self):
        self.text = HELPERS_TPL.read_text(encoding="utf-8")

    def test_helpers_template_exists(self):
        self.assertTrue(HELPERS_TPL.is_file())

    def test_defines_all_seven_named_templates(self):
        for name in self.REQUIRED_NAMES:
            with self.subTest(name=name):
                needle = f'{{{{- define "{name}" -}}}}'
                self.assertIn(needle, self.text, f"missing define for {name}")


class NotesTemplateTests(unittest.TestCase):

    def setUp(self):
        self.text = NOTES_TXT.read_text(encoding="utf-8")

    def test_notes_template_exists(self):
        self.assertTrue(NOTES_TXT.is_file())

    def test_references_cronjob_schedule(self):
        self.assertIn(".Values.cronjob.schedule", self.text)

    def test_references_state_volume_enabled(self):
        self.assertIn(".Values.stateVolume.enabled", self.text)


# --------------------------------------------------------------------------- #
# .helmignore, READMEs                                                        #
# --------------------------------------------------------------------------- #

class HelmIgnoreTests(unittest.TestCase):

    def test_helmignore_exists(self):
        self.assertTrue(HELMIGNORE.is_file(), f"missing: {HELMIGNORE}")


class RootReadmeAnchorTests(unittest.TestCase):

    def setUp(self):
        self.text = ROOT_README.read_text(encoding="utf-8")

    def test_root_readme_has_docker_section_anchor(self):
        self.assertIn("<!-- BEGIN: docker-section (Feature A) -->", self.text)

    def test_root_readme_has_helm_section_anchor(self):
        self.assertIn("<!-- BEGIN: helm-section (Feature B) -->", self.text)

    def test_helm_section_appears_after_docker_section(self):
        docker_idx = self.text.find("<!-- END: docker-section (Feature A) -->")
        helm_idx = self.text.find("<!-- BEGIN: helm-section (Feature B) -->")
        self.assertGreater(docker_idx, -1)
        self.assertGreater(helm_idx, -1)
        self.assertLess(docker_idx, helm_idx, "helm section must come after docker section")


class ChartReadmeTests(unittest.TestCase):

    def setUp(self):
        self.text = CHART_README.read_text(encoding="utf-8")

    def test_chart_readme_exists(self):
        self.assertTrue(CHART_README.is_file(), f"missing: {CHART_README}")

    def test_chart_readme_mentions_helm_install(self):
        self.assertIn("helm install", self.text)

    def test_chart_readme_mentions_oci_registry(self):
        self.assertIn("oci://ghcr.io/asnapper/charts/pipeline-status", self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
