"""Hardware device detection — shared between services that need accelerator selection."""


def detect_device() -> str:
    """
    Auto-detect the best available accelerator.

    Priority: MPS (Apple Silicon) > CUDA (NVIDIA) > CPU.

    This is intentionally minimal: it only returns the device string.
    Docling and Infinity handle their own ONNX/runtime internals;
    this is for code that explicitly needs to choose a backend (e.g. Docling
    AcceleratorOptions).
    """
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
