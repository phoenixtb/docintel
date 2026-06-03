"""
Static-parse tests: verify docker-compose.yml exposes TORCH_INDEX/TORCH_VERSION
as build args and uses ${PROFILE_TAG} in the image field for ML services.
"""
import pathlib
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
GPU_OVERRIDE_FILE = REPO_ROOT / "docker-compose.gpu.yml"

ML_SERVICES = ["rag-service", "data-loader", "ingestion-service"]
GPU_SERVICES = ["rag-service", "ingestion-service"]


def _load_compose(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())


class TestComposeProfileArgs:
    def setup_method(self):
        self.compose = _load_compose(COMPOSE_FILE)
        self.services = self.compose.get("services", {})

    def test_torch_index_build_arg_present(self):
        for svc in ML_SERVICES:
            args = self.services[svc].get("build", {}).get("args", {})
            assert "TORCH_INDEX" in args, f"TORCH_INDEX build arg missing in {svc}"

    def test_torch_version_build_arg_present(self):
        for svc in ML_SERVICES:
            args = self.services[svc].get("build", {}).get("args", {})
            assert "TORCH_VERSION" in args, f"TORCH_VERSION build arg missing in {svc}"

    def test_torch_index_default_uses_env_var(self):
        for svc in ML_SERVICES:
            args = self.services[svc]["build"]["args"]
            val = args["TORCH_INDEX"]
            assert "${TORCH_INDEX" in val, (
                f"{svc} TORCH_INDEX arg must reference ${{TORCH_INDEX}} env var, got: {val}"
            )

    def test_torch_index_fallback_is_cpu(self):
        for svc in ML_SERVICES:
            args = self.services[svc]["build"]["args"]
            val = args["TORCH_INDEX"]
            assert "download.pytorch.org/whl/cpu" in val, (
                f"{svc} TORCH_INDEX fallback must be the cpu wheel index, got: {val}"
            )

    def test_image_field_uses_profile_tag(self):
        for svc in ML_SERVICES:
            image = self.services[svc].get("image", "")
            assert "${PROFILE_TAG" in image, (
                f"{svc} image field must use ${{PROFILE_TAG}}, got: {image}"
            )

    def test_image_field_has_cpu_fallback(self):
        for svc in ML_SERVICES:
            image = self.services[svc].get("image", "")
            assert "cpu" in image, (
                f"{svc} image field must have 'cpu' as fallback in ${{PROFILE_TAG:-cpu}}, got: {image}"
            )

    def test_non_ml_services_have_no_profile_tag(self):
        """Java/nginx services should not have PROFILE_TAG in their image field."""
        non_ml = ["api-gateway", "admin-service", "document-service", "web-ui"]
        for svc in non_ml:
            if svc not in self.services:
                continue
            image = self.services[svc].get("image", "")
            assert "PROFILE_TAG" not in image, (
                f"Non-ML service {svc} should not use PROFILE_TAG, got: {image}"
            )


class TestGpuComposeOverride:
    def setup_method(self):
        self.gpu = _load_compose(GPU_OVERRIDE_FILE)
        self.services = self.gpu.get("services", {})

    def test_gpu_override_has_rag_service(self):
        assert "rag-service" in self.services, "gpu override must include rag-service"

    def test_gpu_override_has_ingestion_service(self):
        assert "ingestion-service" in self.services, "gpu override must include ingestion-service"

    def test_gpu_override_does_not_have_data_loader(self):
        assert "data-loader" not in self.services, (
            "data-loader must NOT be in gpu override (no ML inference at runtime)"
        )

    def test_rag_service_has_gpu_device_reservation(self):
        devices = (
            self.services["rag-service"]
            .get("deploy", {})
            .get("resources", {})
            .get("reservations", {})
            .get("devices", [])
        )
        assert len(devices) > 0, "rag-service must have GPU device reservation in gpu overlay"
        assert any(d.get("driver") == "nvidia" for d in devices), (
            "rag-service GPU reservation must use driver=nvidia"
        )

    def test_ingestion_service_has_gpu_device_reservation(self):
        devices = (
            self.services["ingestion-service"]
            .get("deploy", {})
            .get("resources", {})
            .get("reservations", {})
            .get("devices", [])
        )
        assert len(devices) > 0, "ingestion-service must have GPU device reservation in gpu overlay"
        assert any(d.get("driver") == "nvidia" for d in devices), (
            "ingestion-service GPU reservation must use driver=nvidia"
        )

    def test_gpu_capabilities_include_gpu(self):
        for svc in GPU_SERVICES:
            devices = (
                self.services[svc]
                .get("deploy", {})
                .get("resources", {})
                .get("reservations", {})
                .get("devices", [])
            )
            for device in devices:
                assert "gpu" in device.get("capabilities", []), (
                    f"{svc} GPU device reservation must list 'gpu' in capabilities"
                )
