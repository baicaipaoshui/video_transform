#!/usr/bin/env bash
set -euo pipefail

task_project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
task_requirements="$task_project_root/requirements.txt"
task_venv="$task_project_root/.venv"
task_check_only=0
task_skip_install=0
task_skip_register=0
task_backup_dir=""

usage() {
  cat <<'EOF'
Usage: ./setup_qwenpaw.sh [--check] [--skip-install] [--skip-register]

Environment overrides:
  SCENARIO_PYTHON                 Python used to create .venv (default: python3)
  QWENPAW_BIN                     QwenPaw executable path
  QWENPAW_HOME                    QwenPaw home (default: ~/.qwenpaw)
  QWENPAW_CONFIG                  QwenPaw config.json path
  SCENARIO_VISION_PROVIDER_ID     Vision provider (default: global-video-understanding/agent.json)
  SCENARIO_VISION_MODEL_ID        Vision model (default: global-video-understanding/agent.json)
  SCENARIO_TEXT_PROVIDER_ID       Text provider (default: video-coordinator/agent.json)
  SCENARIO_TEXT_MODEL_ID          Text model (default: video-coordinator/agent.json)

--check verifies an existing installation without changing files.
--skip-install keeps the existing .venv and only configures QwenPaw.
--skip-register updates agent.json paths but does not register Agent profiles.
EOF
}

info() {
  printf '[setup] %s\n' "$*"
}

fail() {
  printf '[setup] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$task_backup_dir" && -d "$task_backup_dir" ]]; then
    rm -rf -- "$task_backup_dir"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      task_check_only=1
      ;;
    --skip-install)
      task_skip_install=1
      ;;
    --skip-register)
      task_skip_register=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown argument: $1"
      ;;
  esac
  shift
done

task_python_bin="${SCENARIO_PYTHON:-python3}"

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is not installed or not in PATH"
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe is not installed or not in PATH"

task_qwenpaw_home="${QWENPAW_HOME:-$HOME/.qwenpaw}"
task_qwenpaw_bin="${QWENPAW_BIN:-}"
if [[ -z "$task_qwenpaw_bin" ]]; then
  if command -v qwenpaw >/dev/null 2>&1; then
    task_qwenpaw_bin="$(command -v qwenpaw)"
  elif [[ -x "$task_qwenpaw_home/bin/qwenpaw" ]]; then
    task_qwenpaw_bin="$task_qwenpaw_home/bin/qwenpaw"
  elif [[ -x "$task_qwenpaw_home/venv/bin/qwenpaw" ]]; then
    task_qwenpaw_bin="$task_qwenpaw_home/venv/bin/qwenpaw"
  else
    fail "QwenPaw is not installed; set QWENPAW_BIN after installing it"
  fi
fi
[[ -x "$task_qwenpaw_bin" ]] || fail "QwenPaw executable is not runnable: $task_qwenpaw_bin"

if [[ "$task_check_only" -eq 0 && "$task_skip_install" -eq 0 ]]; then
  command -v "$task_python_bin" >/dev/null 2>&1 || fail "Python not found: $task_python_bin"
  "$task_python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
    || fail "CPython 3.12 or newer is required by the pinned dependencies"
  if [[ ! -x "$task_venv/bin/python" ]]; then
    info "creating Python environment at $task_venv"
    "$task_python_bin" -m venv "$task_venv"
  fi
  info "installing pinned Python dependencies"
  "$task_venv/bin/python" -m pip install -r "$task_requirements"
fi

[[ -x "$task_venv/bin/python" ]] || fail ".venv is missing; run without --check/--skip-install first"
task_venv_python="$task_venv/bin/python"
"$task_venv_python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
  || fail ".venv must use CPython 3.12 or newer"

agent_model_field() {
  "$task_venv_python" - "$task_project_root/$1/agent.json" "$2" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["active_model"][sys.argv[2]]
if not isinstance(value, str) or not value:
    raise SystemExit(f"invalid active_model.{sys.argv[2]} in {sys.argv[1]}")
print(value)
PY
}

task_vision_provider="${SCENARIO_VISION_PROVIDER_ID:-$(agent_model_field global-video-understanding provider_id)}"
task_vision_model="${SCENARIO_VISION_MODEL_ID:-$(agent_model_field global-video-understanding model)}"
task_text_provider="${SCENARIO_TEXT_PROVIDER_ID:-$(agent_model_field video-coordinator provider_id)}"
task_text_model="${SCENARIO_TEXT_MODEL_ID:-$(agent_model_field video-coordinator model)}"
[[ -n "${SCENARIO_VISION_PROVIDER_ID:-}" || "$task_vision_provider" == "$(agent_model_field video_extractor provider_id)" ]] \
  || fail "vision Agent providers differ; set SCENARIO_VISION_PROVIDER_ID explicitly"
[[ -n "${SCENARIO_VISION_MODEL_ID:-}" || "$task_vision_model" == "$(agent_model_field video_extractor model)" ]] \
  || fail "vision Agent models differ; set SCENARIO_VISION_MODEL_ID explicitly"
[[ -n "${SCENARIO_TEXT_PROVIDER_ID:-}" || "$task_text_provider" == "$(agent_model_field json-coverter provider_id)" ]] \
  || fail "text Agent providers differ; set SCENARIO_TEXT_PROVIDER_ID explicitly"
[[ -n "${SCENARIO_TEXT_MODEL_ID:-}" || "$task_text_model" == "$(agent_model_field json-coverter model)" ]] \
  || fail "text Agent models differ; set SCENARIO_TEXT_MODEL_ID explicitly"
task_config_path="${QWENPAW_CONFIG:-$task_qwenpaw_home/config.json}"

patch_agent_configs() {
  "$task_venv_python" - \
    "$task_project_root" \
    "$task_vision_provider" "$task_vision_model" \
    "$task_text_provider" "$task_text_model" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
vision = {"provider_id": sys.argv[2], "model": sys.argv[3]}
text = {"provider_id": sys.argv[4], "model": sys.argv[5]}
models = {
    "global-video-understanding": vision,
    "video_extractor": vision,
    "video-coordinator": text,
    "json-coverter": text,
}

for agent_id, model in models.items():
    path = root / agent_id / "agent.json"
    if not path.is_file():
        raise SystemExit(f"missing Agent configuration: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected_workspace = str((root / agent_id).resolve())
    changed = data.get("workspace_dir") != expected_workspace or data.get("active_model") != model
    data["workspace_dir"] = expected_workspace
    data["active_model"] = model
    if changed:
        temporary = path.with_name(path.name + ".setup.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        print(f"[setup] updated {path.relative_to(root)}")
PY
}

profile_state() {
  "$task_venv_python" - "$task_config_path" "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
agent_id = sys.argv[2]
expected = str(Path(sys.argv[3]).resolve())
if not config_path.is_file():
    print("absent")
    raise SystemExit
try:
    data = json.loads(config_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    print(f"invalid:{error}")
    raise SystemExit
profile = ((data.get("agents") or {}).get("profiles") or {}).get(agent_id)
if not isinstance(profile, dict):
    print("absent")
elif str(Path(profile.get("workspace_dir", "")).expanduser().resolve()) == expected:
    print("match")
else:
    print("mismatch:" + str(profile.get("workspace_dir", "")))
PY
}

backup_workspace_config() {
  local task_agent_id="$1"
  local task_workspace="$task_project_root/$task_agent_id"
  mkdir -p "$task_backup_dir/$task_agent_id"
  for task_name in agent.json skill.json; do
    if [[ -f "$task_workspace/$task_name" ]]; then
      cp -p "$task_workspace/$task_name" "$task_backup_dir/$task_agent_id/$task_name"
    fi
  done
}

restore_workspace_config() {
  local task_agent_id="$1"
  local task_workspace="$task_project_root/$task_agent_id"
  for task_name in agent.json skill.json; do
    if [[ -f "$task_backup_dir/$task_agent_id/$task_name" ]]; then
      cp -p "$task_backup_dir/$task_agent_id/$task_name" "$task_workspace/$task_name"
    fi
  done
}

register_agent() {
  local task_agent_id="$1"
  local task_agent_name="$2"
  local task_provider="$3"
  local task_model="$4"
  local task_workspace="$task_project_root/$task_agent_id"
  local task_state
  task_state="$(profile_state "$task_agent_id" "$task_workspace")"
  case "$task_state" in
    match)
      info "Agent already registered: $task_agent_id"
      return
      ;;
    absent)
      ;;
    mismatch:*)
      fail "Agent '$task_agent_id' already points to '${task_state#mismatch:}'. Update/remove that QwenPaw profile, then rerun setup."
      ;;
    invalid:*)
      fail "cannot read QwenPaw configuration '$task_config_path': ${task_state#invalid:}"
      ;;
    *)
      fail "unexpected profile state for '$task_agent_id': $task_state"
      ;;
  esac

  info "registering QwenPaw Agent: $task_agent_id"
  backup_workspace_config "$task_agent_id"
  if ! "$task_qwenpaw_bin" agents create \
      --name "$task_agent_name" \
      --agent-id "$task_agent_id" \
      --workspace-dir "$task_workspace" \
      --language zh \
      --template default \
      --provider-id "$task_provider" \
      --model-id "$task_model"; then
    restore_workspace_config "$task_agent_id"
    fail "failed to register '$task_agent_id'; verify that provider '$task_provider' and model '$task_model' exist in QwenPaw"
  fi
  restore_workspace_config "$task_agent_id"
}

check_agent_profiles() {
  local task_agent_id
  for task_agent_id in video-coordinator global-video-understanding video_extractor json-coverter; do
    local task_state
    task_state="$(profile_state "$task_agent_id" "$task_project_root/$task_agent_id")"
    [[ "$task_state" == "match" ]] \
      || fail "QwenPaw profile '$task_agent_id' is not registered to this checkout ($task_state)"
  done
}

if [[ "$task_check_only" -eq 0 ]]; then
  patch_agent_configs
  if [[ "$task_skip_register" -eq 0 ]]; then
    task_backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/scenario-qwenpaw-setup.XXXXXX")"
    register_agent video-coordinator "视频链路协调agent" "$task_text_provider" "$task_text_model"
    register_agent global-video-understanding "全局理解agent" "$task_vision_provider" "$task_vision_model"
    register_agent video_extractor "视频链路提取agent" "$task_vision_provider" "$task_vision_model"
    register_agent json-coverter "json转换" "$task_text_provider" "$task_text_model"
  fi
fi

info "checking Python imports"
"$task_venv_python" -c 'import docx, jsonschema, lxml, numpy, PIL, pydantic, scipy, yaml'

info "checking project structure"
"$task_venv_python" \
  "$task_project_root/global-video-understanding/skills/video-case-orchestrator/scripts/validate_structure.py" \
  >/dev/null

info "checking stable pipeline entrypoint"
"$task_venv_python" \
  "$task_project_root/video-coordinator/skills/video-pipeline/scripts/process.py" \
  --help >/dev/null

if [[ "$task_skip_register" -eq 0 ]]; then
  check_agent_profiles
fi

info "setup verified successfully"
info "run a new Case from: $task_project_root/video-coordinator"
