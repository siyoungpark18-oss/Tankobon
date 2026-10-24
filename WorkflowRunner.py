"""Execute saved tool chains in private working folders and publish the final result."""

import shutil
from pathlib import Path

from ManagerConfig import get_input
from ManagerOutput import (
    STAGING_PREFIX,
    finalize_output,
    get_output,
    settle_runs,
)
from ToolRegistry import TOOL_REGISTRY
from Util import friendly_error, natural_sort_key, throttle_if_needed
from WorkflowStore import validate_workflows


_INTERNAL_NAMES = {".tankobon_run.json"}
_BACKUP_MARKER = ".tankobon-backup-"


class _WorkflowCancelled(Exception):
    pass


class _StepProducedNoResults(Exception):
    def __init__(self, index, tool_id):
        self.index = index
        self.tool_id = tool_id


def _is_internal(path):
    name = Path(path).name
    return (name in _INTERNAL_NAMES or name.startswith(STAGING_PREFIX)
            or _BACKUP_MARKER in name)


def _visible_entries(folder):
    folder = Path(folder)
    try:
        return sorted(
            (item for item in folder.iterdir() if not _is_internal(item)),
            key=lambda item: natural_sort_key(item.name),
        )
    except FileNotFoundError:
        return []


def _copy_entry(source, destination, cancel):
    if cancel is not None and cancel.is_set():
        raise _WorkflowCancelled()
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        print(f"  ⚠ Skipping symbolic link in workflow data: {source.name}")
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in _visible_entries(source):
            _copy_entry(child, destination / child.name, cancel)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_contents(source, destination, cancel):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for item in _visible_entries(source):
        _copy_entry(item, destination / item.name, cancel)


def _remove_stage_contents(stage):
    stage = Path(stage)
    for item in stage.iterdir():
        if item.name == ".tankobon_run.json":
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


def _remove_empty_cancelled_stage(stage):
    stage = Path(stage)
    try:
        entries = list(stage.iterdir())
    except OSError:
        return
    if not any(item.name != ".tankobon_run.json" for item in entries):
        shutil.rmtree(stage, ignore_errors=True)


def _step_config(config, step, registry_entry, input_root, output_root):
    step_config = dict(config)
    step_config.update({
        "input": str(input_root),
        "output": str(output_root),
        "ask_run_name": False,
        "sort_output": False,
        "replace_output": True,
        "auto_clear_input": False,
    })
    config_key = registry_entry.get("config_key")
    if config_key and step.get("option") is not None:
        step_config[config_key] = step["option"]
    return step_config


def run_workflow(config, workflow, cancel=None, registry=None):
    """Run each tool on the prior step's output without changing the real Input folder.

    A new workflow result replaces its previous named result only after every step has
    produced something and all final files have reached the outer output stage.
    """
    registry = registry if registry is not None else TOOL_REGISTRY
    workflow = validate_workflows([workflow], registry=registry)[0]
    source_input = get_input(config)
    if not source_input.is_dir():
        print(f"  Workflow Input folder was not found: {source_input}")
        return False
    if not _visible_entries(source_input):
        print("  Workflow Input is empty; no output was changed.")
        return False

    publish_config = dict(config)
    publish_config["auto_clear_input"] = False
    publish_stage = get_output(publish_config, "Workflows", workflow["name"])
    workspace = publish_stage / ".workflow_work"
    step_index = 0
    step_tool_id = workflow["steps"][0]["tool_id"]

    try:
        current_input_root = workspace / "source"
        _copy_contents(source_input, get_input({"input": str(current_input_root)}), cancel)

        for step_index, step in enumerate(workflow["steps"], 1):
            step_tool_id = step["tool_id"]
            if cancel is not None and cancel.is_set():
                raise _WorkflowCancelled()
            throttle_if_needed(config, cancel)
            registry_entry = registry[step_tool_id]
            handler = registry_entry["handler"]
            output_root = workspace / f"step_{step_index:03d}_results"
            step_config = _step_config(
                config, step, registry_entry, current_input_root, output_root
            )
            label = step_tool_id.replace("_", " ").title()
            print(f"  Workflow step {step_index}/{len(workflow['steps'])}: {label}")
            try:
                handler(step_config, cancel)
            finally:
                settle_runs(
                    step_config,
                    cancelled=cancel is not None and cancel.is_set(),
                    exclude_paths=(publish_stage,),
                )
            if cancel is not None and cancel.is_set():
                raise _WorkflowCancelled()

            result_root = Path(output_root) / "output"
            if not _visible_entries(result_root):
                raise _StepProducedNoResults(step_index, step_tool_id)

            if step_index < len(workflow["steps"]):
                current_input_root = workspace / f"step_{step_index + 1:03d}_input"
                _copy_contents(
                    result_root,
                    get_input({"input": str(current_input_root)}),
                    cancel,
                )
            else:
                _copy_contents(result_root, publish_stage, cancel)

        if cancel is not None and cancel.is_set():
            raise _WorkflowCancelled()
    except _WorkflowCancelled:
        print("  Workflow cancelled; the previous workflow output was kept.")
        shutil.rmtree(workspace, ignore_errors=True)
        finalize_output(publish_config, publish_stage, cancel)
        _remove_empty_cancelled_stage(publish_stage)
        return False
    except _StepProducedNoResults as error:
        failed_label = error.tool_id.replace("_", " ").title()
        print(
            f"  Workflow stopped at step {error.index} ({failed_label}): "
            "no results were produced. The previous workflow output was kept."
        )
        shutil.rmtree(workspace, ignore_errors=True)
        finalize_output(publish_config, publish_stage)
        return False
    except Exception as error:
        failed_label = step_tool_id.replace("_", " ").title()
        print(
            f"  Workflow stopped at step {step_index or 1} ({failed_label}): "
            f"{friendly_error(error)}. The previous workflow output was kept."
        )
        shutil.rmtree(workspace, ignore_errors=True)
        _remove_stage_contents(publish_stage)
        finalize_output(publish_config, publish_stage)
        return False

    shutil.rmtree(workspace, ignore_errors=True)
    published = finalize_output(publish_config, publish_stage, cancel)
    if published:
        print(f"  Workflow complete: {workflow['name']}")
    return published
