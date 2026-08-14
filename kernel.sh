#!/usr/bin/env bash
# kernel.sh — trusted harness; never mounted writable inside a generation.
# Contract: one generation = one organism.el load = one lineage observation.
set -euo pipefail

INSTRUMENT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINEAGE="${LINEAGE:?path to lineage worktree}"
GITDIR="${GITDIR:-$LINEAGE.git}"
JOURNAL="$LINEAGE/journal.md"
OBSERVATION="${OBSERVATION:-$LINEAGE.observations}"
OBSERVATION_BRANCH="${OBSERVATION_BRANCH:-}"
METADATA_NAME=".ouroboros-lineage.json"
GENERATIONS="${GENERATIONS:-10}"
WALL="${WALL:-600}"
CPU_BUDGET_SECONDS="${CPU_BUDGET_SECONDS:-120}"
MEMORY_MAX="${MEMORY_MAX:-1G}"
MEMORY_SWAP_MAX="${MEMORY_SWAP_MAX:-0}"
TASKS_MAX="${TASKS_MAX:-64}"
WORKTREE_MAX_BYTES="${WORKTREE_MAX_BYTES:-268435456}"
WORKTREE_MAX_FILES="${WORKTREE_MAX_FILES:-10000}"
TMP_MAX_BYTES="${TMP_MAX_BYTES:-67108864}"
RUN_MAX_BYTES="${RUN_MAX_BYTES:-16777216}"
MODEL_PROVIDER="${MODEL_PROVIDER:-anthropic}"
MODEL_NAME="${MODEL_NAME:-}"
MODEL_MAX_OUTPUT_TOKENS="${MODEL_MAX_OUTPUT_TOKENS:-12000}"
MODEL_REQUEST_TIMEOUT="${MODEL_REQUEST_TIMEOUT:-600}"
MODEL_MAX_PROMPT_BYTES="${MODEL_MAX_PROMPT_BYTES:-196608}"
MODEL_BUDGET_USD="${MODEL_BUDGET_USD:-1.00}"
MODEL_INPUT_USD_PER_MTOK="${MODEL_INPUT_USD_PER_MTOK:-}"
MODEL_OUTPUT_USD_PER_MTOK="${MODEL_OUTPUT_USD_PER_MTOK:-}"
PYTHON="${PYTHON:-python3}"

MODEL_BROKER="$INSTRUMENT_DIR/model_broker.py"
RUNTIME="$INSTRUMENT_DIR/runtime.py"
SANDBOX_RUNNER="$INSTRUMENT_DIR/sandbox_runner.sh"
RESOURCE_RUNNER=()
GENERATION_UNIT=""
CONSTRAINTS_JSON=""

G() { git --git-dir="$GITDIR" --work-tree="$LINEAGE" "$@"; }
O() { git -C "$OBSERVATION" "$@"; }

positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

for value in "$GENERATIONS" "$WALL" "$CPU_BUDGET_SECONDS" \
  "$WORKTREE_MAX_BYTES" "$WORKTREE_MAX_FILES" "$TMP_MAX_BYTES" \
  "$RUN_MAX_BYTES" "$MODEL_MAX_OUTPUT_TOKENS" "$MODEL_REQUEST_TIMEOUT" \
  "$MODEL_MAX_PROMPT_BYTES" "$TASKS_MAX"; do
  if ! positive_integer "$value"; then
    echo "kernel: integer limits must be positive" >&2
    exit 2
  fi
done

case "$MODEL_PROVIDER" in
  anthropic)
    API_KEY_NAME=ANTHROPIC_API_KEY
    API_ORIGIN=https://api.anthropic.com
    MODEL_NAME="${MODEL_NAME:-claude-opus-4-8}"
    case "$MODEL_NAME" in
      claude-opus-4-8|claude-opus-4-7|claude-opus-4-6)
        MODEL_INPUT_USD_PER_MTOK="${MODEL_INPUT_USD_PER_MTOK:-5.00}"
        MODEL_OUTPUT_USD_PER_MTOK="${MODEL_OUTPUT_USD_PER_MTOK:-25.00}"
        ;;
    esac
    ;;
  openai)
    API_KEY_NAME=OPENAI_API_KEY
    API_ORIGIN=https://api.openai.com
    MODEL_NAME="${MODEL_NAME:-gpt-5.6}"
    case "$MODEL_NAME" in
      gpt-5.6|gpt-5.6-sol)
        MODEL_INPUT_USD_PER_MTOK="${MODEL_INPUT_USD_PER_MTOK:-5.00}"
        MODEL_OUTPUT_USD_PER_MTOK="${MODEL_OUTPUT_USD_PER_MTOK:-30.00}"
        ;;
      gpt-5.6-terra)
        MODEL_INPUT_USD_PER_MTOK="${MODEL_INPUT_USD_PER_MTOK:-2.50}"
        MODEL_OUTPUT_USD_PER_MTOK="${MODEL_OUTPUT_USD_PER_MTOK:-15.00}"
        ;;
      gpt-5.6-luna)
        MODEL_INPUT_USD_PER_MTOK="${MODEL_INPUT_USD_PER_MTOK:-1.00}"
        MODEL_OUTPUT_USD_PER_MTOK="${MODEL_OUTPUT_USD_PER_MTOK:-6.00}"
        ;;
    esac
    ;;
  *)
    echo "kernel: MODEL_PROVIDER must be 'anthropic' or 'openai'" >&2
    exit 2
    ;;
esac

if [ -z "$MODEL_INPUT_USD_PER_MTOK" ] || [ -z "$MODEL_OUTPUT_USD_PER_MTOK" ]; then
  echo "kernel: explicit token prices are required for unrecognized models" >&2
  exit 2
fi

CPU_QUOTA_VALUE="$(awk -v cpu="$CPU_BUDGET_SECONDS" -v wall="$WALL" \
  'BEGIN { print int((cpu / wall) * 100) }')"
if [ "$CPU_QUOTA_VALUE" -lt 1 ]; then
  echo "kernel: CPU_BUDGET_SECONDS must be at least 1% of WALL" >&2
  exit 2
fi
CPU_QUOTA_PERCENT="${CPU_QUOTA_VALUE}%"
RESOURCE_RUNNER=(
  systemd-run --user --scope --quiet --collect --expand-environment=no
  -p "CPUQuota=$CPU_QUOTA_PERCENT"
  -p "MemoryMax=$MEMORY_MAX"
  -p "MemorySwapMax=$MEMORY_SWAP_MAX"
  -p "TasksMax=$TASKS_MAX"
)

CONSTRAINTS_JSON="$($RUNTIME constraints-json \
  --wall "$WALL" --cpu-seconds "$CPU_BUDGET_SECONDS" \
  --cpu-quota-percent "$CPU_QUOTA_VALUE" --memory-max "$MEMORY_MAX" \
  --memory-swap-max "$MEMORY_SWAP_MAX" --tasks-max "$TASKS_MAX" \
  --work-bytes "$WORKTREE_MAX_BYTES" --work-files "$WORKTREE_MAX_FILES" \
  --tmp-bytes "$TMP_MAX_BYTES" --run-bytes "$RUN_MAX_BYTES" \
  --model-prompt-bytes "$MODEL_MAX_PROMPT_BYTES" \
  --model-output-tokens "$MODEL_MAX_OUTPUT_TOKENS" \
  --model-timeout "$MODEL_REQUEST_TIMEOUT" --model-budget "$MODEL_BUDGET_USD")"

API_KEY="${!API_KEY_NAME:-}"
LOG="$(mktemp)"
BROKER_LOG="$(mktemp)"
MODEL_AUDIT="$(mktemp)"
BASE_ARCHIVE="$(mktemp)"
RESULT_ARCHIVE="$(mktemp)"
ROOT_METADATA="$(mktemp)"
JOURNAL_BASE="$(mktemp)"
PUBLISH_PARENT=""
BROKER_PID=""
BROKER_DIR=""
BROKER_SOCKET=""

release_generation_unit() {
  if [ -n "$GENERATION_UNIT" ]; then
    systemctl --user stop "$GENERATION_UNIT.service" >/dev/null 2>&1 || true
    systemctl --user reset-failed "$GENERATION_UNIT.service" >/dev/null 2>&1 || true
    GENERATION_UNIT=""
  fi
}

stop_broker() {
  if [ -n "$BROKER_PID" ]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
    BROKER_PID=""
  fi
  if [ -n "$BROKER_DIR" ]; then
    rm -f -- "$BROKER_DIR/model.sock" "$BROKER_DIR/capabilities.json" \
      "$BROKER_DIR/base.tar" "$BROKER_DIR/journal.md" \
      "$BROKER_DIR/lineage.json" "$BROKER_DIR/sandbox_runner.sh"
    rmdir -- "$BROKER_DIR" 2>/dev/null || true
    BROKER_DIR=""
    BROKER_SOCKET=""
  fi
}

cleanup() {
  stop_broker
  release_generation_unit
  rm -f -- "$LOG" "$BROKER_LOG" "$BASE_ARCHIVE" "$RESULT_ARCHIVE" \
    "$ROOT_METADATA" "$JOURNAL_BASE" "$MODEL_AUDIT"
  if [ -n "$PUBLISH_PARENT" ]; then
    rm -rf -- "$PUBLISH_PARENT"
  fi
}
trap cleanup EXIT

instrument_fingerprint() {
  "$RUNTIME" fingerprint "$INSTRUMENT_DIR/kernel.sh" "$MODEL_BROKER" \
    "$RUNTIME" "$SANDBOX_RUNNER"
}

instrument_commit() {
  if [ -n "${OUROBOROS_INSTRUMENT_COMMIT:-}" ]; then
    printf '%s\n' "$OUROBOROS_INSTRUMENT_COMMIT"
    return
  fi
  if ! git -C "$INSTRUMENT_DIR" diff --quiet -- || \
     ! git -C "$INSTRUMENT_DIR" diff --cached --quiet --; then
    echo "kernel: instrument checkout has uncommitted tracked changes" >&2
    return 1
  fi
  git -C "$INSTRUMENT_DIR" rev-parse --verify HEAD
}

validate_lineage() {
  if [ ! -d "$LINEAGE" ] || [ ! -d "$GITDIR" ]; then
    echo "kernel: lineage is not initialized; use init-lineage.sh" >&2
    return 1
  fi
  if [ ! -r "$LINEAGE/organism.el" ] || [ ! -r "$JOURNAL" ]; then
    echo "kernel: lineage is missing organism.el or journal.md" >&2
    return 1
  fi
  root="$(G rev-list --max-parents=0 HEAD | head -n 1)"
  if [ -z "$root" ] || ! G show "$root:$METADATA_NAME" > "$ROOT_METADATA" 2>/dev/null; then
    echo "kernel: lineage predates this instrument; start a new lineage" >&2
    return 1
  fi
  expected="$($RUNTIME read-field "$ROOT_METADATA" instrument_fingerprint)" || return 1
  actual="$(instrument_fingerprint)"
  if [ "$expected" != "$actual" ]; then
    echo "kernel: instrument changed since this lineage was initialized" >&2
    echo "kernel: start a new lineage; do not continue it under different physics" >&2
    return 1
  fi
  expected_commit="$($RUNTIME read-field "$ROOT_METADATA" instrument_commit)" || return 1
  actual_commit="$(instrument_commit)" || return 1
  if [ "$expected_commit" != "$actual_commit" ]; then
    echo "kernel: instrument commit differs from the lineage root" >&2
    echo "kernel: checkout $expected_commit before extending this lineage" >&2
    return 1
  fi
  if [ ! -d "$OBSERVATION/.git" ] || [ ! -r "$OBSERVATION/metadata.json" ]; then
    echo "kernel: trusted observation repository is missing" >&2
    return 1
  fi
  observed_commit="$($RUNTIME read-observation-field \
    "$OBSERVATION/metadata.json" instrument_commit)" || return 1
  if [ "$observed_commit" != "$actual_commit" ]; then
    echo "kernel: observation branch belongs to a different instrument commit" >&2
    return 1
  fi
  expected_lineage_branch="$($RUNTIME read-observation-field \
    "$OBSERVATION/metadata.json" lineage_branch)" || return 1
  expected_observation_branch="$($RUNTIME read-observation-field \
    "$OBSERVATION/metadata.json" observation_branch)" || return 1
  actual_lineage_branch="$(G branch --show-current)"
  actual_observation_branch="$(O branch --show-current)"
  if [ "$actual_lineage_branch" != "$expected_lineage_branch" ]; then
    echo "kernel: lineage repository is on $actual_lineage_branch, expected $expected_lineage_branch" >&2
    return 1
  fi
  if [ "$actual_observation_branch" != "$expected_observation_branch" ] || \
     { [ -n "$OBSERVATION_BRANCH" ] && \
       [ "$actual_observation_branch" != "$OBSERVATION_BRANCH" ]; }; then
    echo "kernel: observation repository is on the wrong branch" >&2
    return 1
  fi
}

start_broker() {
  generation="$1"
  stop_broker
  : > "$BROKER_LOG"
  : > "$MODEL_AUDIT"
  BROKER_DIR="$(mktemp -d)"
  BROKER_SOCKET="$BROKER_DIR/model.sock"

  {
    printf '%s\n' "$MODEL_PROVIDER"
    printf '%s\n' "$MODEL_NAME"
    printf '%s\n' "$API_KEY"
    printf '%s\n' "$MODEL_MAX_OUTPUT_TOKENS"
    printf '%s\n' "$MODEL_REQUEST_TIMEOUT"
    printf '%s\n' "$MODEL_MAX_PROMPT_BYTES"
    printf '%s\n' "$MODEL_BUDGET_USD"
    printf '%s\n' "$MODEL_INPUT_USD_PER_MTOK"
    printf '%s\n' "$MODEL_OUTPUT_USD_PER_MTOK"
  } | env -i PATH="$PATH" "$PYTHON" "$MODEL_BROKER" --socket "$BROKER_SOCKET" \
        --constraints-json "$CONSTRAINTS_JSON" --audit-log "$MODEL_AUDIT" \
        --generation "$generation" \
        >"$BROKER_LOG" 2>&1 &
  BROKER_PID=$!

  for _ in {1..100}; do
    [ -S "$BROKER_SOCKET" ] && return 0
    if ! kill -0 "$BROKER_PID" 2>/dev/null; then
      echo "kernel: model broker exited during startup" >&2
      tail -n 10 "$BROKER_LOG" >&2
      stop_broker
      return 1
    fi
    sleep 0.05
  done
  echo "kernel: model broker did not become ready" >&2
  tail -n 10 "$BROKER_LOG" >&2
  stop_broker
  return 1
}

prepare_generation_inputs() {
  : > "$BASE_ARCHIVE"
  G archive --format=tar -o "$BASE_ARCHIVE" HEAD
  cp -a -- "$BASE_ARCHIVE" "$BROKER_DIR/base.tar"
  cp -a -- "$JOURNAL" "$BROKER_DIR/journal.md"
  cp -a -- "$ROOT_METADATA" "$BROKER_DIR/lineage.json"
  cp -a -- "$SANDBOX_RUNNER" "$BROKER_DIR/sandbox_runner.sh"
  chmod 0444 "$BROKER_DIR/base.tar" "$BROKER_DIR/journal.md" \
    "$BROKER_DIR/lineage.json" "$BROKER_DIR/sandbox_runner.sh"
}

unit_property() {
  printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n 1
}

numeric_or_empty() {
  case "$1" in
    ''|'[not set]'|18446744073709551615) printf '\n' ;;
    *[!0-9]*) printf '\n' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

run_generation() {
  archive_limit_blocks="$1"
  generation="$2"
  GENERATION_UNIT="ouroboros-generation-$$-$generation"
  SYSTEMD_RESULT="start-failed"
  CPU_USAGE_NSEC=""
  MEMORY_PEAK_BYTES=""
  OOM_KILLS=""

  if ! systemd-run --user --quiet --remain-after-exit \
      --expand-environment=no --unit="$GENERATION_UNIT" \
      -p Type=exec -p KillMode=control-group -p "RuntimeMaxSec=$WALL" \
      -p "CPUQuota=$CPU_QUOTA_PERCENT" -p "MemoryMax=$MEMORY_MAX" \
      -p "MemorySwapMax=$MEMORY_SWAP_MAX" -p "TasksMax=$TASKS_MAX" \
      -p "StandardOutput=truncate:$RESULT_ARCHIVE" \
      -p "StandardError=truncate:$LOG" \
      /bin/sh -c 'ulimit -f "$1"; shift; exec "$@"' ouroboros \
      "$archive_limit_blocks" bwrap \
        --clearenv \
        --setenv HOME /work --setenv PATH /usr/bin:/bin \
        --ro-bind /usr /usr \
        --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
        --symlink usr/bin /bin --symlink usr/sbin /sbin \
        --proc /proc --dev /dev \
        --size "$TMP_MAX_BYTES" --tmpfs /tmp \
        --size "$RUN_MAX_BYTES" --tmpfs /run \
        --size "$WORKTREE_MAX_BYTES" --tmpfs /work \
        --ro-bind "$BROKER_DIR" /kernel \
        --unshare-net --unshare-pid --unshare-ipc --unshare-uts \
        --die-with-parent \
        /bin/sh /kernel/sandbox_runner.sh; then
    echo "kernel: could not start generation resource unit" >> "$LOG"
    return 125
  fi

  deadline=$((SECONDS + WALL + 30))
  while :; do
    state="$(systemctl --user show "$GENERATION_UNIT.service" \
      -p ActiveState -p SubState 2>/dev/null || true)"
    active="$(unit_property "$state" ActiveState)"
    sub="$(unit_property "$state" SubState)"
    if [ "$sub" = "exited" ] || [ "$active" = "failed" ] || \
       [ "$active" = "inactive" ]; then
      break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      systemctl --user stop "$GENERATION_UNIT.service" >/dev/null 2>&1 || true
      SYSTEMD_RESULT="observer-timeout"
      return 124
    fi
    sleep 0.05
  done

  properties="$(systemctl --user show "$GENERATION_UNIT.service" \
    -p Result -p ExecMainCode -p ExecMainStatus -p CPUUsageNSec \
    -p MemoryPeak -p OOMKills 2>/dev/null || true)"
  SYSTEMD_RESULT="$(unit_property "$properties" Result)"
  main_code="$(unit_property "$properties" ExecMainCode)"
  main_status="$(unit_property "$properties" ExecMainStatus)"
  CPU_USAGE_NSEC="$(numeric_or_empty "$(unit_property "$properties" CPUUsageNSec)")"
  MEMORY_PEAK_BYTES="$(numeric_or_empty "$(unit_property "$properties" MemoryPeak)")"
  OOM_KILLS="$(numeric_or_empty "$(unit_property "$properties" OOMKills)")"

  if [ "$SYSTEMD_RESULT" = "timeout" ]; then
    return 124
  fi
  if [ "$main_code" = "1" ] && [[ "$main_status" =~ ^[0-9]+$ ]]; then
    return "$main_status"
  fi
  if [ "$main_code" = "2" ] && [[ "$main_status" =~ ^[0-9]+$ ]]; then
    return $((128 + main_status))
  fi
  return 125
}

doctor() {
  echo "host:        $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
  echo "emacs:       $(emacs --version 2>/dev/null | head -1 || echo MISSING)"
  echo "bwrap:       $(command -v bwrap || echo MISSING)"
  echo "curl:        $(command -v curl || echo MISSING)"
  echo "git:         $(command -v git || echo MISSING)"
  echo "systemd-run: $(command -v systemd-run || echo MISSING)"
  echo "python:      $("$PYTHON" --version 2>&1 || echo MISSING)"
  echo "provider:    $MODEL_PROVIDER (kernel-only)"
  echo "model:       $MODEL_NAME (kernel-only)"
  echo "budget:      USD $MODEL_BUDGET_USD per generation"
  echo "pricing:     USD $MODEL_INPUT_USD_PER_MTOK/$MODEL_OUTPUT_USD_PER_MTOK per MTok input/output"
  echo "worktree:    $WORKTREE_MAX_BYTES bytes, $WORKTREE_MAX_FILES published files"
  echo "CPU:         $CPU_BUDGET_SECONDS aggregate seconds via CPUQuota=$CPU_QUOTA_PERCENT and wall=$WALL"
  echo "$API_KEY_NAME: $([ -n "$API_KEY" ] && echo present || echo MISSING)"
  echo -n "cgroup:      "
  if "${RESOURCE_RUNNER[@]}" /usr/bin/true 2>/dev/null; then
    echo "ok"
  else
    echo "FAILED"
  fi
  echo -n "host API reachability: "
  if curl -sS -o /dev/null --max-time 20 "$API_ORIGIN/" 2>/dev/null; then
    echo "ok"
  else
    echo "FAILED"
  fi
}

if [ "${1:-}" = "--doctor" ]; then
  doctor
  exit 0
fi

for required in "$MODEL_BROKER" "$RUNTIME" "$SANDBOX_RUNNER"; do
  if [ ! -r "$required" ]; then
    echo "kernel: trusted runtime file is missing: $required" >&2
    exit 2
  fi
done
if [ -z "$API_KEY" ]; then
  echo "kernel: $API_KEY_NAME is required for MODEL_PROVIDER=$MODEL_PROVIDER" >&2
  exit 2
fi
if ! command -v "$PYTHON" >/dev/null 2>&1 || \
   ! command -v systemd-run >/dev/null 2>&1 || \
   ! command -v systemctl >/dev/null 2>&1; then
  echo "kernel: python3 and a working user systemd manager are required" >&2
  exit 2
fi
if ! validate_lineage; then
  exit 2
fi
if ! "${RESOURCE_RUNNER[@]}" /usr/bin/true; then
  echo "kernel: could not create the configured aggregate resource scope" >&2
  exit 2
fi

for _ in $(seq "$GENERATIONS"); do
  # Explicit out-of-loop edits are context, committed before speculative execution.
  G add -A -f
  G diff --cached --quiet || G commit -qm "external edit"

  gen=$(( $(G rev-list --count --grep='^gen ' HEAD) + 1 ))
  generation_started_at="$(date -Is)"
  generation_started_ns="$(date +%s%N)"
  cp -a -- "$JOURNAL" "$JOURNAL_BASE"
  : > "$LOG"
  : > "$RESULT_ARCHIVE"
  start_broker "$gen"
  prepare_generation_inputs

  archive_limit_bytes=$((WORKTREE_MAX_BYTES + WORKTREE_MAX_FILES * 1024 + 1048576))
  archive_limit_blocks=$(((archive_limit_bytes + 511) / 512))

  set +e
  run_generation "$archive_limit_blocks" "$gen"
  rc=$?
  set -e
  stop_broker
  release_generation_unit
  generation_duration_ms=$((($(date +%s%N) - generation_started_ns) / 1000000))
  result_archive_bytes="$(stat -c %s "$RESULT_ARCHIVE")"

  if [ "$rc" -ne 0 ]; then
    prev="$(G rev-list -n 2 HEAD -- organism.el | tail -n 1)"
    if [ -n "$prev" ]; then
      G checkout -q "$prev" -- organism.el
    fi
    {
      echo
      echo "## gen $gen — died — $(date -Is)"
      echo "exit $rc"
      echo '~~~'
      tail -n 5 "$LOG"
      echo '~~~'
    } >> "$JOURNAL"
    G add -A -f
    G commit -qm "gen $gen: died (exit $rc); staged writes discarded"
    if [ "${OOM_KILLS:-0}" -gt 0 ] 2>/dev/null; then
      outcome="cgroup_oom"
    elif [ "$rc" -eq 124 ]; then
      outcome="wall_timeout"
    elif [ "$rc" -ge 128 ]; then
      outcome="signal"
    else
      outcome="process_exit"
    fi
    observation_args=(
      "$RUNTIME" write-generation-observation
      "$OBSERVATION/generations/$(printf '%04d' "$gen").json"
      --audit "$MODEL_AUDIT" --stderr "$LOG" --generation "$gen"
      --started-at "$generation_started_at" --duration-ms "$generation_duration_ms"
      --outcome "$outcome" --exit-status "$rc"
      --systemd-result "${SYSTEMD_RESULT:-unknown}"
      --lineage-commit "$(G rev-parse HEAD)"
      --result-archive-bytes "$result_archive_bytes"
      --provider "$MODEL_PROVIDER" --model "$MODEL_NAME"
      --constraints-json "$CONSTRAINTS_JSON"
    )
    [ -n "${CPU_USAGE_NSEC:-}" ] && observation_args+=(--cpu-usage-nsec "$CPU_USAGE_NSEC")
    [ -n "${MEMORY_PEAK_BYTES:-}" ] && observation_args+=(--memory-peak-bytes "$MEMORY_PEAK_BYTES")
    [ -n "${OOM_KILLS:-}" ] && observation_args+=(--oom-kills "$OOM_KILLS")
    "${observation_args[@]}"
    O add "generations/$(printf '%04d' "$gen").json"
    O commit -qm "observe: gen $gen $outcome"
    continue
  fi

  PUBLISH_PARENT="$(mktemp -d)"
  if ! publication_summary="$($RUNTIME extract-result \
      "$RESULT_ARCHIVE" "$PUBLISH_PARENT/tree" \
      --max-bytes "$WORKTREE_MAX_BYTES" --max-files "$WORKTREE_MAX_FILES" \
      2>>"$LOG")"; then
    "$RUNTIME" write-generation-observation \
      "$OBSERVATION/generations/$(printf '%04d' "$gen").json" \
      --audit "$MODEL_AUDIT" --stderr "$LOG" --generation "$gen" \
      --started-at "$generation_started_at" --duration-ms "$generation_duration_ms" \
      --outcome decoder_rejection --exit-status 0 \
      --systemd-result "${SYSTEMD_RESULT:-unknown}" \
      --lineage-commit "$(G rev-parse HEAD)" \
      --result-archive-bytes "$result_archive_bytes" \
      --provider "$MODEL_PROVIDER" --model "$MODEL_NAME" \
      --constraints-json "$CONSTRAINTS_JSON"
    O add "generations/$(printf '%04d' "$gen").json"
    O commit -qm "observe: gen $gen decoder-rejection"
    echo "kernel: trusted result decoder rejected a nominally successful generation" >&2
    exit 2
  fi
  read -r published_bytes published_files < <(
    "$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["bytes"], d["files"])' \
      "$publication_summary"
  )

  # Replace the host worktree only after the sandbox and trusted decoder succeed.
  G rm -qrf --ignore-unmatch .
  cp -a -- "$PUBLISH_PARENT/tree/." "$LINEAGE/"
  cp -a -- "$ROOT_METADATA" "$LINEAGE/$METADATA_NAME"
  cp -a -- "$JOURNAL_BASE" "$JOURNAL"
  G add -A -f

  changed_paths="$(G diff --cached --name-only)"
  if [ -z "$changed_paths" ]; then
    heading="no-change"
    subject="gen $gen: no-change"
  else
    path_count="$(printf '%s\n' "$changed_paths" | wc -l)"
    organism_stat="$(G diff --cached --numstat -- organism.el | \
      awk '{print "+"$1"/-"$2}')"
    if [ -n "$organism_stat" ]; then
      detail="organism $organism_stat; $path_count paths"
    else
      detail="organism unchanged; $path_count other paths"
    fi
    heading="changed — $detail"
    subject="gen $gen: changed ($detail)"
  fi

  printf '\n## gen %s — %s — %s\n' "$gen" "$heading" "$(date -Is)" >> "$JOURNAL"
  G add -A -f
  G commit -qm "$subject"

  observation_args=(
    "$RUNTIME" write-generation-observation
    "$OBSERVATION/generations/$(printf '%04d' "$gen").json"
    --audit "$MODEL_AUDIT" --stderr "$LOG" --generation "$gen"
    --started-at "$generation_started_at" --duration-ms "$generation_duration_ms"
    --outcome "$heading" --exit-status 0
    --systemd-result "${SYSTEMD_RESULT:-unknown}"
    --lineage-commit "$(G rev-parse HEAD)"
    --result-archive-bytes "$result_archive_bytes"
    --published-bytes "$published_bytes" --published-files "$published_files"
    --provider "$MODEL_PROVIDER" --model "$MODEL_NAME"
    --constraints-json "$CONSTRAINTS_JSON"
  )
  [ -n "${CPU_USAGE_NSEC:-}" ] && observation_args+=(--cpu-usage-nsec "$CPU_USAGE_NSEC")
  [ -n "${MEMORY_PEAK_BYTES:-}" ] && observation_args+=(--memory-peak-bytes "$MEMORY_PEAK_BYTES")
  [ -n "${OOM_KILLS:-}" ] && observation_args+=(--oom-kills "$OOM_KILLS")
  "${observation_args[@]}"
  O add "generations/$(printf '%04d' "$gen").json"
  O commit -qm "observe: gen $gen $heading"

  rm -rf -- "$PUBLISH_PARENT"
  PUBLISH_PARENT=""
done
