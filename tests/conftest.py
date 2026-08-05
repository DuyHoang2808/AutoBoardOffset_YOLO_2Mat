"""Cho phép các test import trực tiếp module trong gateway/ và fiducial_detector/
mà không cần cài đặt package (chúng không phải là package, chỉ là script FastAPI)."""

import os
import sys

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)

GATEWAY_DIR = os.path.join(REPO_ROOT, "gateway")
FIDUCIAL_DIR = os.path.join(REPO_ROOT, "fiducial_detector")

for _p in (GATEWAY_DIR, FIDUCIAL_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def vrs_calib_json_path() -> str:
    return os.path.join(GATEWAY_DIR, "vrs_calib_4diem.json")
