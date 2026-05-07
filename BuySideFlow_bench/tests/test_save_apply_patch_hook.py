from pathlib import Path
from types import SimpleNamespace

from sweagent.run.hooks.apply_patch import SaveApplyPatchHook
from sweagent.types import AgentRunResult


def test_save_apply_patch_uses_result_instance_id(tmp_path: Path):
    hook = SaveApplyPatchHook(show_success_message=False)
    hook.on_init(run=SimpleNamespace(output_dir=tmp_path))

    hook.on_instance_start(index=0, env=SimpleNamespace(repo=None), problem_statement=SimpleNamespace(id="fund_042"))
    hook.on_instance_start(index=1, env=SimpleNamespace(repo=None), problem_statement=SimpleNamespace(id="stock_102"))

    result = AgentRunResult(
        info={"instance_id": "fund_042", "submission": "fund patch", "exit_status": "submitted"},
        trajectory=[],
    )
    hook.on_instance_completed(result=result)

    fund_patch = tmp_path / "fund_042" / "fund_042.patch"
    stock_patch = tmp_path / "stock_102" / "stock_102.patch"

    assert fund_patch.read_text(encoding="utf-8") == "fund patch"
    assert not stock_patch.exists()
