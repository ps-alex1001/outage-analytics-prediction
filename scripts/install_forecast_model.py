"""Install a Colab native serving export after checksum and prediction-parity checks."""
import argparse
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.forecast_model import ForecastModel


def install(archive, target, expected_sha256=None):
    archive, target = Path(archive), Path(target)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected_sha256 and checksum != expected_sha256:
        raise ValueError("Archive checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    names = {"manifest.json", "model.ubj", "parity.json"}
    with TemporaryDirectory(prefix="forecast-install-", dir=target.parent) as tmp:
        staging = Path(tmp) / "model"
        staging.mkdir()
        with zipfile.ZipFile(archive) as package:
            if len(package.namelist()) != 3 or set(package.namelist()) != names:
                raise ValueError("Expected exactly manifest.json, model.ubj and parity.json")
            if sum(info.file_size for info in package.infolist()) > 10_000_000:
                raise ValueError("Unexpected model export size")
            for name in names:
                (staging / name).write_bytes(package.read(name))
        model = ForecastModel(staging)
        if target.exists():
            if not all((target / name).is_file() and (target / name).read_bytes() == (staging / name).read_bytes()
                       for name in names):
                raise ValueError("Destination differs; install into a new directory and set FORECAST_MODEL_DIR")
        else:
            staging.rename(target)
    return {"model_id": model.manifest["model_id"], "parity_rows_verified": model.parity_rows,
            "archive_sha256": checksum, "installed_at": str(target.resolve())}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / "data/models/outage_v2_xgboost")
    parser.add_argument("--sha256")
    args = parser.parse_args()
    print(json.dumps(install(args.archive, args.target, args.sha256), indent=2))
