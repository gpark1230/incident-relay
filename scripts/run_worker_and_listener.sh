#!/bin/bash
# Runs the RQ worker and the event listener as two processes in one
# container. If either exits, this script exits too, so Railway's
# restart policy restarts the whole container and both processes come
# back up together -- avoids one silently dying while the other keeps
# running in a half-working state.
set -e

python -m app.worker &
WORKER_PID=$!

python -m app.listener &
LISTENER_PID=$!

wait -n "$WORKER_PID" "$LISTENER_PID"
exit $?
