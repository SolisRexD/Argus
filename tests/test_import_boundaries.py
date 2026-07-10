import os
from pathlib import Path
import subprocess
import sys


def test_data_pipeline_import_does_not_require_unreal():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(root / "scripts"), env.get("PYTHONPATH", "")]
    )
    code = """
import sys
from argus_core.io import read_pose_rows
from argus_components.data_pipeline import DataPipelineService
from argus_components import DataPipelineService as ExportedDataPipelineService

assert DataPipelineService is ExportedDataPipelineService
assert callable(read_pose_rows)
assert "unreal" not in sys.modules
print("import-boundary-ok")
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-boundary-ok"
