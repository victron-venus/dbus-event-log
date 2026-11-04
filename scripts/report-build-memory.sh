#!/usr/bin/env bash
# Read-only, bounded diagnostics; never wrap or retry the shipping build.
set -eu
[[ $# == 2 && $1 =~ ^(before|after)$ && $2 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || exit 2
printf 'Build memory snapshot: %s\n' "$1"
awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/' /proc/meminfo || true
cat /proc/pressure/memory 2>/dev/null || true
for field in memory.current memory.peak memory.max memory.swap.current memory.swap.max memory.events; do
    if [[ -r /sys/fs/cgroup/$field ]]; then
        printf 'Host cgroup %s: ' "$field"
        tr '\n' ' ' < "/sys/fs/cgroup/$field"
        printf '\n'
    fi
done
container="buildx_buildkit_${2}0"
# Select only numeric resource limits and the OOM flag; never dump container configuration.
if ! timeout --kill-after=2s 5s docker inspect --format \
    'BuildKit memory_limit={{.HostConfig.Memory}} memory_swap_limit={{.HostConfig.MemorySwap}} oom_killed={{.State.OOMKilled}}' \
    "$container" 2>/dev/null; then
    printf 'BuildKit limits unavailable\n'
fi
# Cgroup v2 counters are hierarchical and include child build processes.
# Missing counters and a dead builder remain diagnostic observations, not build success.
if ! timeout --kill-after=2s 5s docker exec "$container" sh -c '
    for field in memory.current memory.peak memory.max memory.swap.current memory.swap.max memory.events; do
        if [ -r "/sys/fs/cgroup/$field" ]; then
            printf "BuildKit cgroup %s: " "$field"
            tr "\n" " " < "/sys/fs/cgroup/$field"
            printf "\n"
        fi
    done
' 2>/dev/null; then
    printf 'BuildKit counters unavailable\n'
fi
