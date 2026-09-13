#!/usr/bin/env bash
# BloomIndex: Qdrant Server management script (Docker or Podman).
#
# Manages a single Qdrant container alongside the backend. Operations
# are idempotent: starting an already running container is a no-op, and
# re-running on a stopped container resumes it without data loss.
#
# Runtime selection:
#   QDRANT_RUNTIME=auto    (default) try podman, fall back to docker
#   QDRANT_RUNTIME=podman  force podman
#   QDRANT_RUNTIME=docker  force docker
#
# Docker and Podman share CLI syntax for container lifecycle commands.
# Runtime-specific branching is only required for daemon reachability:
#   * docker: requires a reachable daemon socket or rootless setup.
#   * podman: daemonless; requires an initialized storage namespace.
#
# Usage:
#   ./scripts/qdrant.sh start    # Start (creates on first run)
#   ./scripts/qdrant.sh stop     # Stop, preserving on-disk storage
#   ./scripts/qdrant.sh status   # Show container health and counts
#   ./scripts/qdrant.sh restart  # Stop and restart container
#   ./scripts/qdrant.sh logs     # Follow container log output
#   ./scripts/qdrant.sh remove   # Stop and delete container only
#
# Environment variable overrides (with defaults):
#   QDRANT_RUNTIME      auto
#   QDRANT_CONTAINER    bloomindex-qdrant
#   QDRANT_STORAGE_DIR  ~/.local/share/bloomindex/qdrant_storage
#   QDRANT_VERSION      v1.19.1
#   QDRANT_PORT_REST    6333
#   QDRANT_PORT_GRPC    6334
#
# Podman notes:
#   * --restart unless-stopped requires enabling the restart unit:
#     systemctl --user enable --now podman-restart.service
#   * On SELinux systems, append :Z to the volume mount if permission
#     is denied on /qdrant/storage.
#
# Does not require root or sudo privileges.

set -euo pipefail

NAME="${QDRANT_CONTAINER:-bloomindex-qdrant}"
STORAGE_DIR="${QDRANT_STORAGE_DIR:-${HOME}/.local/share/bloomindex/qdrant_storage}"
VERSION="${QDRANT_VERSION:-v1.19.1}"
PORT_REST="${QDRANT_PORT_REST:-6333}"
PORT_GRPC="${QDRANT_PORT_GRPC:-6334}"
IMAGE="qdrant/qdrant:${VERSION}"

REST_URL="http://localhost:${PORT_REST}"

# Runtime selection.
QDRANT_RUNTIME="${QDRANT_RUNTIME:-auto}"
case "$QDRANT_RUNTIME" in
    auto)
        if command -v podman >/dev/null 2>&1; then
            RUNTIME=podman
        elif command -v docker >/dev/null 2>&1; then
            RUNTIME=docker
        else
            echo "ERROR: neither podman nor docker found in PATH." >&2
            echo "  Install one of:" >&2
            echo "    https://podman.io/docs/installation" >&2
            echo "    https://docs.docker.com/get-docker/" >&2
            exit 1
        fi
        ;;
    podman|docker)
        RUNTIME="$QDRANT_RUNTIME"
        if ! command -v "$RUNTIME" >/dev/null 2>&1; then
            echo "ERROR: ${RUNTIME} not found in PATH (QDRANT_RUNTIME=${QDRANT_RUNTIME})." >&2
            exit 1
        fi
        ;;
    *)
        echo "ERROR: QDRANT_RUNTIME must be one of: auto, podman, docker (got '${QDRANT_RUNTIME}')." >&2
        exit 1
        ;;
esac

# Prerequisite checks.
# Docker requires a reachable daemon. Podman is daemonless; podman info
# verifies user storage and namespace initialization.
if [[ "$RUNTIME" == "docker" ]]; then
    if ! docker ps >/dev/null 2>&1; then
        echo "ERROR: 'docker ps' failed. The Docker daemon is unreachable or" >&2
        echo "  your user can't talk to it. Either:" >&2
        echo "    (a) add your user to the docker group: sudo usermod -aG docker \$USER" >&2
        echo "        (then log out and back in), OR" >&2
        echo "    (b) use rootless docker: https://docs.docker.com/engine/security/rootless/" >&2
        echo "  Or set QDRANT_RUNTIME=podman to use Podman instead." >&2
        exit 1
    fi
else
    if ! podman info >/dev/null 2>&1; then
        echo "ERROR: 'podman info' failed. Podman is installed but its user" >&2
        echo "  storage / namespaces aren't initialized. Try:" >&2
        echo "    podman system reset           # nuclear: wipe + reinit" >&2
        echo "  Or on first run (macOS/Windows):" >&2
        echo "    podman machine init && podman machine start" >&2
        exit 1
    fi
fi

# Helper functions.
container_state() {
    # Filter by exact container name rather than inspect to avoid false
    # matches against images, volumes, or networks sharing the name.
    # Empty output unambiguously indicates the container is absent.
    local line
    line=$("$RUNTIME" ps -a --filter "name=^${NAME}$" --format '{{.State}}' 2>/dev/null || true)
    if [[ -z "$line" ]]; then
        echo "absent"
    else
        echo "$line" | tr '[:upper:]' '[:lower:]'
    fi
}

print_urls() {
    echo "REST:    ${REST_URL}"
    echo "Web UI:  ${REST_URL}/dashboard"
    echo "gRPC:    localhost:${PORT_GRPC}"
    echo
    echo "Point BloomIndex at it by setting (in .env / .env.<profile> or shell):"
    echo "  QDRANT_URL=${REST_URL}"
}

wait_for_health() {
    # Polls /healthz for up to 15s. Returns 0 on success, 1 on timeout.
    if ! command -v curl >/dev/null 2>&1; then
        echo "(curl not found; skipping health probe)"
        return 0
    fi
    for _ in $(seq 1 30); do
        if curl -fsS "${REST_URL}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Command handlers.
cmd_start() {
    state=$(container_state)
    case "$state" in
        running)
            echo "Already running."
            ;;
        exited|created|paused)
            echo "Starting existing container '${NAME}' (state: ${state})..."
            "$RUNTIME" start "$NAME" >/dev/null
            ;;
        absent)
            mkdir -p "$STORAGE_DIR"
            echo "Creating container '${NAME}' from image ${IMAGE} (runtime: ${RUNTIME})..."
            echo "  Storage: ${STORAGE_DIR}"
            "$RUNTIME" run -d \
                --name "$NAME" \
                -p "${PORT_REST}:6333" -p "${PORT_GRPC}:6334" \
                -v "${STORAGE_DIR}:/qdrant/storage" \
                --restart unless-stopped \
                "$IMAGE" >/dev/null
            ;;
        *)
            echo "ERROR: container in unexpected state '${state}'." >&2
            echo "Try: ${RUNTIME} inspect ${NAME}" >&2
            exit 1
            ;;
    esac

    if wait_for_health; then
        echo "Qdrant is healthy."
    else
        echo "WARN: /healthz did not respond within 15s. Check '${RUNTIME} logs ${NAME}'." >&2
    fi
    echo
    print_urls
}

cmd_stop() {
    state=$(container_state)
    if [[ "$state" == "running" ]]; then
        "$RUNTIME" stop "$NAME" >/dev/null
        echo "Stopped (state preserved on disk at ${STORAGE_DIR})."
    else
        echo "Not running (state: ${state})."
    fi
}

cmd_status() {
    state=$(container_state)
    echo "Runtime:   ${RUNTIME}"
    echo "Container: ${NAME}"
    echo "State:     ${state}"
    echo "Image:     ${IMAGE}"
    echo "Storage:   ${STORAGE_DIR}"
    if [[ "$state" == "running" ]]; then
        echo "Ports:     REST=${PORT_REST}  gRPC=${PORT_GRPC}"
        echo
        if command -v curl >/dev/null 2>&1; then
            if curl -fsS "${REST_URL}/healthz" >/dev/null 2>&1; then
                echo "Health:    OK (${REST_URL}/healthz)"
                if command -v python3 >/dev/null 2>&1; then
                    count=$(curl -fsS "${REST_URL}/collections" 2>/dev/null \
                            | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("result",{}).get("collections",[])))' \
                            2>/dev/null || echo "?")
                    echo "Collections: ${count}"
                fi
            else
                echo "Health:    UNREACHABLE -- check '${RUNTIME} logs ${NAME}'"
            fi
        fi
        echo
        print_urls
    fi
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_logs() {
    "$RUNTIME" logs -f --tail 200 "$NAME"
}

cmd_remove() {
    cmd_stop
    if [[ "$(container_state)" != "absent" ]]; then
        "$RUNTIME" rm "$NAME" >/dev/null
        echo "Container '${NAME}' removed."
    fi
    echo "Storage at ${STORAGE_DIR} preserved (delete manually if you want a clean slate)."
}

usage() {
    cat >&2 <<EOF
Usage: $0 {start|stop|status|restart|logs|remove}

  start    Start the Qdrant container (creates on first run; idempotent)
  stop     Stop the container; on-disk storage preserved
  status   Show container state, health, collection count, URLs
  restart  Stop + start
  logs     Tail container logs (Ctrl+C to exit)
  remove   Stop + delete container; on-disk storage preserved

Settings via env vars:
  QDRANT_RUNTIME       auto | docker | podman   (default: auto)
  QDRANT_CONTAINER     (default: bloomindex-qdrant)
  QDRANT_STORAGE_DIR   (default: \$HOME/.local/share/bloomindex/qdrant_storage)
  QDRANT_VERSION       (default: v1.19.1)
  QDRANT_PORT_REST     (default: 6333)
  QDRANT_PORT_GRPC     (default: 6334)
EOF
    exit 2
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    logs)    cmd_logs ;;
    remove)  cmd_remove ;;
    -h|--help|help) usage ;;
    *)       usage ;;
esac
