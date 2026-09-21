"""Lua scripts for the task state transitions that previously did a plain
GET-modify-SET from Python -- three separate round trips with a window
between them where another process could touch the same task key.

The one that actually bit us: the reaper reclaims a task whose lease
expired and hands it to a new worker, but the *original* (slow, not dead)
worker can still be mid-flight and calls ack()/nack() afterwards. Without
a check, that stale call would silently clobber whatever the new claim
had already written -- including a legitimate second attempt's result.
Every task carries a `lease_token` (a fresh random value each time it's
claimed); ack/nack only apply if the caller's token still matches the
token currently on the task, i.e. nobody has reclaimed it out from under
them since. Redis runs each script as a single atomic step, so there's no
gap for a concurrent writer to land in the middle of a read-modify-write.
"""

# KEYS[1] = task key
# ARGV[1] = worker_id, ARGV[2] = now_ts, ARGV[3] = lease_seconds,
# ARGV[4] = new_lease_token, ARGV[5] = ttl_seconds
CLAIM_TASK = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local task = cjson.decode(raw)
task['attempt_count'] = task['attempt_count'] + 1
task['status'] = 'running'
task['assigned_worker'] = ARGV[1]
task['lease_expires_at'] = tonumber(ARGV[2]) + tonumber(ARGV[3])
task['lease_token'] = ARGV[4]
task['updated_at'] = tonumber(ARGV[2])
local encoded = cjson.encode(task)
redis.call('SET', KEYS[1], encoded, 'EX', ARGV[5])
return encoded
"""

# KEYS[1] = task key, KEYS[2] = processing key
# ARGV[1] = task_id, ARGV[2] = result_json, ARGV[3] = now_ts,
# ARGV[4] = ttl_seconds, ARGV[5] = expected_lease_token ('' to skip check)
ACK_TASK = """
local raw = redis.call('GET', KEYS[1])
if not raw then
  redis.call('LREM', KEYS[2], 1, ARGV[1])
  return 0
end
local task = cjson.decode(raw)
if ARGV[5] ~= '' and task['lease_token'] ~= ARGV[5] then
  return 0
end
task['status'] = 'done'
task['result'] = cjson.decode(ARGV[2])
task['lease_expires_at'] = cjson.null
task['lease_token'] = cjson.null
task['updated_at'] = tonumber(ARGV[3])
redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[4])
redis.call('LREM', KEYS[2], 1, ARGV[1])
return 1
"""

# KEYS[1] = task key, KEYS[2] = processing key, KEYS[3] = dead-letter key
# ARGV[1] = task_id, ARGV[2] = error, ARGV[3] = now_ts, ARGV[4] = ttl_seconds,
# ARGV[5] = expected_lease_token ('' to skip check), ARGV[6] = pending key prefix
NACK_TASK = """
local raw = redis.call('GET', KEYS[1])
if not raw then
  redis.call('LREM', KEYS[2], 1, ARGV[1])
  return cjson.encode({ok=false})
end
local task = cjson.decode(raw)
if ARGV[5] ~= '' and task['lease_token'] ~= ARGV[5] then
  return cjson.encode({ok=false, reason='stale'})
end
redis.call('LREM', KEYS[2], 1, ARGV[1])
task['lease_expires_at'] = cjson.null
task['lease_token'] = cjson.null
task['error'] = ARGV[2]
task['updated_at'] = tonumber(ARGV[3])
local final_status
if task['attempt_count'] < task['max_attempts'] and not task['cancel_requested'] then
  task['status'] = 'pending'
  task['assigned_worker'] = cjson.null
  redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[4])
  redis.call('RPUSH', ARGV[6] .. task['required_capability'], ARGV[1])
  final_status = 'pending'
else
  if task['cancel_requested'] then
    task['status'] = 'cancelled'
  else
    task['status'] = 'failed'
  end
  redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[4])
  redis.call('RPUSH', KEYS[3], ARGV[1])
  final_status = task['status']
end
return cjson.encode({ok=true, status=final_status})
"""

# KEYS[1] = task key, KEYS[2] = pending key (this task's own capability queue)
# ARGV[1] = task_id, ARGV[2] = now_ts, ARGV[3] = ttl_seconds
RELEASE_LEASE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local task = cjson.decode(raw)
task['status'] = 'pending'
task['assigned_worker'] = cjson.null
task['lease_expires_at'] = cjson.null
task['lease_token'] = cjson.null
task['updated_at'] = tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[3])
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""

# KEYS[1] = task key
# ARGV[1] = expected_lease_token, ARGV[2] = now_ts, ARGV[3] = lease_seconds,
# ARGV[4] = ttl_seconds
RENEW_LEASE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local task = cjson.decode(raw)
if task['lease_token'] ~= ARGV[1] then return 0 end
task['lease_expires_at'] = tonumber(ARGV[2]) + tonumber(ARGV[3])
task['updated_at'] = tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[4])
return 1
"""

# KEYS[1] = task key, KEYS[2] = pending key for this task's capability
# ARGV[1] = task_id, ARGV[2] = now_ts, ARGV[3] = ttl_seconds
REQUEST_CANCEL = """
local raw = redis.call('GET', KEYS[1])
if not raw then return cjson.encode({ok=false}) end
local task = cjson.decode(raw)
if task['status'] == 'done' or task['status'] == 'failed' or task['status'] == 'cancelled' then
  return cjson.encode({ok=false})
end
task['cancel_requested'] = true
task['updated_at'] = tonumber(ARGV[2])
local was_pending = task['status'] == 'pending'
if was_pending then
  redis.call('LREM', KEYS[2], 1, ARGV[1])
  task['status'] = 'cancelled'
end
redis.call('SET', KEYS[1], cjson.encode(task), 'EX', ARGV[3])
return cjson.encode({ok=true, was_pending=was_pending})
"""
