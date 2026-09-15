"""Compare the default OCR path and verify the actual frozen OCR payload.

Uses generated images only, with no accounts, network requests or model
downloads. Source comparison requires the pinned ddddocr and its dependencies;
package inspection requires the same Python/PyInstaller used for the build.
"""

import argparse
from importlib import metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {"cv2", "ddddocr", "paddle", "paddleocr", "paddlex"}


def run_check(command, *, frozen):
    """Use an empty profile and a bounded, hidden child, including for onefile."""
    with tempfile.TemporaryDirectory(prefix="chaoxing-ocr-check-") as profile:
        report_path = Path(profile) / "ocr-result.json"
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                           CHAOXING_DATA_DIR=profile, CHAOXING_HEADLESS="1")
        environment.pop("CHAOXING_TAURI", None)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        # os.environ normalizes Windows key case; a copied dict does not.
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        if frozen and os.name == "nt":
            environment["PATH"] = os.pathsep.join((str(system_root / "System32"), str(system_root)))
        process = subprocess.Popen(
            [*command, str(report_path)], cwd=profile, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            # Kill the owned bootloader tree, not just the onefile parent.
            if os.name == "nt":
                subprocess.run([str(system_root / "System32/taskkill.exe"),
                                "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.kill()
            process.communicate(timeout=15)
            raise RuntimeError("OCR diagnostic timed out")
        if process.returncode != 0:
            raise RuntimeError(f"OCR diagnostic exited {process.returncode}: {stdout}\n{stderr}")
        result = json.loads(report_path.read_text(encoding="utf-8"))
        if (result.get("success") is not True or result.get("frozen") is not frozen
                or result.get("prediction") != "1234"
                or result.get("providers") != ["CPUExecutionProvider"]):
            raise RuntimeError(f"Unexpected OCR diagnostic result: {result}")
        if set(Path(profile).iterdir()) != {report_path}:
            raise RuntimeError("OCR diagnostic unexpectedly wrote application state")
        return result


def compare_source():
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from ddddocr import DdddOcr
    from api.captcha_ocr import CaptchaOcr, DDDDOCR_VERSION, _preprocess_image

    if metadata.version("ddddocr") != DDDDOCR_VERSION:
        raise RuntimeError("The reference ddddocr version differs from the adapter")
    reference, adapted = DdddOcr(show_ad=False), CaptchaOcr()
    if tuple(reference.get_charset()) != adapted.charset:
        raise RuntimeError("Default character table differs from upstream")
    cases = []
    for text in ("1234", "8877", "1122", "AbC9", "Zx82", "2026"):
        original = Image.new("RGB", (160, 60), "white")
        ImageDraw.Draw(original).text((8, 4), text, font=ImageFont.load_default(size=40), fill="black")
        for mode in ("RGB", "L", "RGBA", "P"):
            for size in ((160, 60), (107, 39)):
                image = original.resize(size).convert(mode)
                if mode == "RGBA":
                    image.putalpha(128)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
                np.testing.assert_array_equal(
                    _preprocess_image(data), reference.ocr_engine._preprocess_image(image, False),
                )
                expected = reference.classification(data)
                actual = adapted.classification(data)
                if actual != expected:
                    raise RuntimeError(f"Recognition differs for {text}/{mode}/{size}: {expected!r} != {actual!r}")
                cases.append({"input": text, "mode": mode, "size": size, "prediction": actual})

    # A fresh interpreter forbids the full ddddocr and OpenCV imports. Merely
    # checking sys.modules after comparing with the reference would be invalid.
    blocker = """
import builtins, sys
sys.path.insert(0, sys.argv[1])
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'cv2', 'ddddocr'}:
        raise ImportError('Excluded dependency: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from api.captcha_ocr import check_captcha_ocr
raise SystemExit(check_captcha_ocr(sys.argv[2]))
"""
    diagnostic = run_check([sys.executable, "-c", blocker, str(ROOT)], frozen=False)
    return {
        "success": True, "comparison_cases": len(cases), "tensor_equality": "exact",
        "cases": cases, "without_opencv_or_ddddocr_import": diagnostic,
        "versions": {name: metadata.version(name) for name in ("ddddocr", "numpy", "onnxruntime", "Pillow")},
        "scope": "Synthetic regression and execution checks; not real-world captcha accuracy acceptance",
    }


def verify_executable(executable):
    from PyInstaller.archive.readers import CArchiveReader

    executable = executable.resolve(strict=True)
    archive = CArchiveReader(str(executable))
    pyz_name = next(name for name, entry in archive.toc.items() if entry[-1] == "z")
    modules = set(archive.open_embedded_archive(pyz_name).toc)
    excluded_modules = sorted(name for name in modules if name.split(".")[0].lower() in EXCLUDED)
    if excluded_modules or "api.captcha_ocr" not in modules:
        raise RuntimeError(f"Invalid OCR module payload; excluded modules: {excluded_modules}")
    names = {name.replace("\\", "/") for name in archive.toc}
    onefile = "ddddocr/common_old.onnx" in names
    files = [executable]
    if not onefile:
        internal = executable.parent / "_internal"
        files = [path for path in executable.parent.rglob("*") if path.is_file()]
        names.update(path.relative_to(internal).as_posix() for path in internal.rglob("*") if path.is_file())
    required = {
        "ddddocr/common_old.onnx", "ddddocr/charsets.py",
        "ddddocr-1.6.1.dist-info/METADATA", "ddddocr-1.6.1.dist-info/licenses/LICENSE",
        "resource/licenses/ddddocr-LICENSE.txt",
    }
    if not required.issubset(names):
        raise RuntimeError(f"Missing OCR assets: {sorted(required - names)}")
    unwanted = []
    for name in names:
        parts = name.lower().split("/")
        if (parts[-1] in {"common.onnx", "common_det.onnx"}
                or any(part == "cv2" or part.startswith("opencv") or part.startswith("paddle") for part in parts)):
            unwanted.append(name)
    if unwanted:
        raise RuntimeError(f"Unused OCR dependencies were bundled: {sorted(unwanted)}")
    diagnostic = run_check([str(executable), "--check-captcha-ocr"], frozen=True)
    return {
        "success": True, "executable": str(executable), "layout": "onefile" if onefile else "onedir",
        "files": len(files), "bytes": sum(path.stat().st_size for path in files),
        "excluded_modules": excluded_modules, "unused_ocr_files": unwanted, "diagnostic": diagnostic,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", action="store_true")
    mode.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = compare_source() if arguments.source else verify_executable(arguments.executable)
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
