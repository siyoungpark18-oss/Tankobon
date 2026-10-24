"""Versioned storage and validation for user-defined tool workflows."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from ManagerConfig import CONFIG_PATH
from ManagerOutput import validate_run_name


WORKFLOW_FORMAT_VERSION = 1
WORKFLOWS_PATH = CONFIG_PATH.parent / "workflows.json"


class WorkflowStoreError(ValueError):
    """A saved workflow file or definition is invalid."""


def new_workflow(name="", steps=None):
    """Create an unsaved workflow definition with a stable ID."""
    return {
        "id": uuid.uuid4().hex,
        "name": str(name).strip(),
        "steps": list(steps or []),
    }


def _get_registry(registry):
    if registry is not None:
        return registry
    from ToolRegistry import TOOL_REGISTRY
    return TOOL_REGISTRY


def validate_workflows(workflows, registry=None):
    """Return clean workflow dictionaries or raise with a specific validation error."""
    registry = _get_registry(registry)
    if not isinstance(workflows, list):
        raise WorkflowStoreError("Workflows must be a list.")

    validated, seen_ids, seen_names = [], set(), set()
    for workflow_index, workflow in enumerate(workflows, 1):
        prefix = f"Workflow {workflow_index}"
        if not isinstance(workflow, dict):
            raise WorkflowStoreError(f"{prefix} must be an object.")
        if set(workflow) - {"id", "name", "steps"}:
            raise WorkflowStoreError(f"{prefix} contains unsupported fields.")

        workflow_id = workflow.get("id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise WorkflowStoreError(f"{prefix} needs an ID.")
        workflow_id = workflow_id.strip()
        if workflow_id in seen_ids:
            raise WorkflowStoreError(f"Workflow ID '{workflow_id}' is used more than once.")
        seen_ids.add(workflow_id)

        name = workflow.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowStoreError(f"{prefix} needs a name.")
        name = name.strip()
        try:
            validate_run_name(name)
        except ValueError as error:
            raise WorkflowStoreError(f"{prefix} name is invalid: {error}") from error
        name_key = name.casefold()
        if name_key in seen_names:
            raise WorkflowStoreError(f"Workflow name '{name}' is already in use.")
        seen_names.add(name_key)

        steps = workflow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise WorkflowStoreError(f"Workflow '{name}' needs at least one tool step.")
        clean_steps = []
        for step_index, step in enumerate(steps, 1):
            step_prefix = f"Workflow '{name}', step {step_index}"
            if not isinstance(step, dict) or set(step) - {"tool_id", "option"}:
                raise WorkflowStoreError(f"{step_prefix} is invalid.")
            tool_id = step.get("tool_id")
            entry = registry.get(tool_id) if isinstance(tool_id, str) else None
            if not entry or not callable(entry.get("handler")):
                raise WorkflowStoreError(f"{step_prefix} refers to an unavailable tool.")

            options = entry.get("options", [])
            option = step.get("option")
            if option is not None and option not in options:
                raise WorkflowStoreError(f"{step_prefix} has an invalid option.")
            if option is not None and not entry.get("config_key"):
                raise WorkflowStoreError(f"{step_prefix} does not support a saved option.")
            clean_steps.append({"tool_id": tool_id, "option": option})

        validated.append({"id": workflow_id, "name": name, "steps": clean_steps})
    return validated


def load_workflows(path=None, registry=None):
    """Load workflow definitions; a missing file is a normal empty state."""
    path = Path(path) if path is not None else WORKFLOWS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WorkflowStoreError(f"Could not read saved workflows: {error}") from error
    if not isinstance(data, dict) or data.get("version") != WORKFLOW_FORMAT_VERSION:
        raise WorkflowStoreError("The saved workflow file has an unsupported format version.")
    if "workflows" not in data:
        raise WorkflowStoreError("The saved workflow file is missing its workflow list.")
    return validate_workflows(data["workflows"], registry=registry)


def save_workflows(workflows, path=None, registry=None):
    """Validate and atomically replace the user's workflow file."""
    path = Path(path) if path is not None else WORKFLOWS_PATH
    workflows = validate_workflows(workflows, registry=registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"version": WORKFLOW_FORMAT_VERSION, "workflows": workflows},
                stream,
                indent=2,
                ensure_ascii=False,
            )
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
