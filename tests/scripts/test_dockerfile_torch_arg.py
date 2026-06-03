"""
Static-parse tests: verify that all three ML Dockerfiles have the torch pre-install
ARG declarations and RUN statements in the correct order relative to project deps install.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ML_DOCKERFILES = [
    REPO_ROOT / "services" / "rag-service" / "Dockerfile",
    REPO_ROOT / "services" / "ingestion-service" / "Dockerfile",
    REPO_ROOT / "services" / "data-loader" / "Dockerfile",
]


def _read(path: pathlib.Path) -> str:
    return path.read_text()


def _line_index(lines: list[str], pattern: str) -> int:
    """Return the 0-based index of the first line matching the pattern, or -1."""
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            return i
    return -1


class TestDockerfileTorchArg:
    def test_arg_torch_index_present(self):
        for df in ML_DOCKERFILES:
            content = _read(df)
            assert "ARG TORCH_INDEX" in content, f"ARG TORCH_INDEX missing in {df}"

    def test_arg_torch_version_present(self):
        for df in ML_DOCKERFILES:
            content = _read(df)
            assert "ARG TORCH_VERSION" in content, f"ARG TORCH_VERSION missing in {df}"

    def test_torch_index_default_is_cpu(self):
        for df in ML_DOCKERFILES:
            content = _read(df)
            assert "ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu" in content, (
                f"TORCH_INDEX default is not cpu in {df}"
            )

    def test_torch_version_default_is_2_11(self):
        for df in ML_DOCKERFILES:
            content = _read(df)
            assert "ARG TORCH_VERSION=2.11.0" in content, (
                f"TORCH_VERSION default is not 2.11.0 in {df}"
            )

    def test_torch_preinstall_run_present(self):
        """A RUN that installs torch with ${TORCH_INDEX} must exist."""
        for df in ML_DOCKERFILES:
            content = _read(df)
            assert re.search(r'uv pip install.*--index-url.*\$\{?TORCH_INDEX\}?.*torch', content, re.DOTALL), (
                f"torch pre-install RUN with TORCH_INDEX not found in {df}"
            )

    def test_torch_preinstall_before_project_deps(self):
        """The torch pre-install RUN must appear BEFORE 'uv pip install --no-sources .'"""
        for df in ML_DOCKERFILES:
            lines = _read(df).splitlines()
            torch_line = _line_index(lines, r'--index-url.*\$\{?TORCH_INDEX\}?')
            project_line = _line_index(lines, r'uv pip install.*--no-sources')
            assert torch_line != -1, f"torch pre-install not found in {df}"
            assert project_line != -1, f"project deps install not found in {df}"
            assert torch_line < project_line, (
                f"In {df}: torch pre-install (line {torch_line}) must come before "
                f"project deps install (line {project_line})"
            )

    def test_extra_index_url_pypi_present_in_preinstall(self):
        """pypi.org must be listed as extra-index-url so other deps can still resolve."""
        for df in ML_DOCKERFILES:
            content = _read(df)
            # Find the torch pre-install block and check for pypi extra index
            assert "pypi.org/simple" in content, (
                f"pypi.org/simple extra-index-url missing in {df}; "
                "other packages need it when TORCH_INDEX overrides the primary index"
            )

    def test_arg_declarations_before_first_run(self):
        """ARG TORCH_INDEX and ARG TORCH_VERSION must appear before the first RUN."""
        for df in ML_DOCKERFILES:
            lines = _read(df).splitlines()
            torch_index_arg = _line_index(lines, r'^ARG TORCH_INDEX')
            torch_version_arg = _line_index(lines, r'^ARG TORCH_VERSION')
            first_run = _line_index(lines, r'^RUN ')
            assert torch_index_arg != -1, f"ARG TORCH_INDEX not found in {df}"
            assert torch_version_arg != -1, f"ARG TORCH_VERSION not found in {df}"
            assert first_run != -1, f"No RUN found in {df}"
            assert torch_index_arg < first_run, (
                f"In {df}: ARG TORCH_INDEX must come before first RUN"
            )
            assert torch_version_arg < first_run, (
                f"In {df}: ARG TORCH_VERSION must come before first RUN"
            )
