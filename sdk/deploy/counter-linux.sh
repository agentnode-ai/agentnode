#!/bin/bash
# The counter-checks, on the platform where this line exists. A block that does not run its test
# proves nothing and says so: "no tests ran" is not a failure, it is an absence.
cd /root/cc/sdk || exit 1
PY=/opt/agentnode/venv/bin/python
FILES="agentnode_sdk/worker/protocol.py agentnode_sdk/worker/service.py agentnode_sdk/worker/remote.py"
mkdir -p /root/cc/bak
keep() { cp "$1" "/root/cc/bak/$(echo "$1" | tr / _)"; }
back() { cp "/root/cc/bak/$(echo "$1" | tr / _)" "$1"; }
for f in $FILES; do keep "$f"; done

revert() { $PY "/root/cc/reverts/$1" || { echo "  THE REVERT DID NOT LAND -- proves nothing"; return 1; }; }
lane() {
  find /root/cc/sdk -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
  PYTHONPATH=/root/cc/sdk PYTHONDONTWRITEBYTECODE=1 $PY -m pytest -q -p no:randomly "$1" -k "$2" > /root/cc/out.txt 2>&1
  local code=$?
  local ran; ran=$(grep -cE "^[0-9]+ (passed|failed)" /root/cc/out.txt)
  tail -2 /root/cc/out.txt
  if [ "$code" -eq 0 ]; then echo "  exit: 0  <-- REQUIRED NON-ZERO. This block proves nothing."
  elif [ "$ran" -eq 0 ]; then echo "  exit: $code but NO TEST RAN. This block proves nothing."
  else echo "  exit: $code  (a named test went red, as required)"; fi
}
A=tests/test_socket_worker.py
run() { echo; echo "## $1"; revert "$2" && lane $A "$3"; back "$4"; }

echo "# Counter-checks for the socket between the control plane and the worker, on Linux."
run "Z-a. a message is PARSED BEFORE it is shown to be ours" za.py "test_the_parser_is_never_reached_without_the_key" agentnode_sdk/worker/protocol.py
run "Z-b. a message SEEN BEFORE is seen as new" zb.py "test_a_repeat_is_seen" agentnode_sdk/worker/protocol.py
run "Z-c. a connection from ANY ACCOUNT is answered" zc.py "test_a_connection_from_another_account_is_answered_with_nothing" agentnode_sdk/worker/service.py
run "Z-d. a REFUSAL that is not about the job becomes a job that failed" zd.py "test_a_refusal_that_is_not_about_the_job_is_nobody_saying_anything" agentnode_sdk/worker/remote.py
run "Z-e. the socket is reachable by EVERY ACCOUNT" ze.py "test_owner_and_group_and_nobody_else" agentnode_sdk/worker/service.py
run "Z-f. a frame is ALLOCATED FOR before its length is judged" zf.py "test_a_frame_larger_than_anyone_may_send_is_refused_before_it_is_allocated_for" agentnode_sdk/worker/protocol.py
run "Z-g. a job COMMAND need not be a list of arguments" zg.py "test_a_command_that_is_not_a_list_of_arguments" agentnode_sdk/worker/protocol.py
run "Z-h. the TOPOLOGY is what the worker says about itself" zh.py "test_the_client_does_not_ask_the_worker_where_it_is" agentnode_sdk/worker/remote.py
run "Z-i. a worker that ACCEPTS and then says nothing becomes a job that ran and failed" zi.py "test_a_worker_that_accepts_and_never_answers_is_not_a_job_that_ran" agentnode_sdk/worker/remote.py
run "Z-j. a field the wire does not describe is IGNORED rather than refused" zj.py "test_a_field_this_build_does_not_describe" agentnode_sdk/worker/protocol.py

echo; echo "## restored"
for f in $FILES; do back "$f"; cmp -s "$f" "/root/cc/bak/$(echo "$f" | tr / _)" && echo "  $f restored" || echo "  $f DIFFERS"; done
