# demo-seed — realistic demo data through the front door (issue #72)

Seeds a SocTalk install with significant alert/investigation volume with
ZERO runtime LLM inference: every event enters via the real adapter API
with real occurrence timestamps, authorization facts via the real facts
API, and the reasoning tail is triaged by a real runs-worker whose only
reachable "model" is `provider.py` — a deterministic OpenAI-compatible
playback server serving prose authored offline (Claude/Codex
subscription work, never API tokens). All replay beats, guard rulings,
terminal closes, and fleet counters are genuinely emitted by the
pipeline; the only substitution is where the model text comes from.

Disclosure: verdict/supervisor prose on seeded demo tenants is scripted
playback, not live inference. Never present demo films as live model
output.

## Pieces

- `vendor_goldens.py` — regenerates `corpus/goldens/` (checksummed
  snapshot of soctalk-goldens `data_m0`; see MANIFEST.json). Requires the
  sibling checkout; goldens data dirs are gitignored there by design.
- `families.py` — authored event families + goldens-derived cases, built
  to the product's verified gates (severity/MITRE bands, rule-202
  operational groups, live authz-binding vocabulary action=rule_id).
- `provider.py` — the scripted provider. `uvicorn provider:app --port 8091`
  (env `SEED_MANIFEST` points at the manifest `seed.py` writes).
- `seed.py` — `run` (plan → manifest → facts → inject) and `verify`
  (fleet-day/live counters via the MSSP API).

## Local rehearsal (verify-skill stack)

1. Stand up Postgres+API per `.claude/skills/verify/SKILL.md`; seed an
   admin, create a tenant, mint an adapter + worker token.
2. `uvicorn provider:app --port 8091` (from this directory).
3. Run the worker with NO provider keys in env — only:
   `SOCTALK_LLM_PROVIDER=openai OPENAI_BASE_URL=http://127.0.0.1:8091/v1
   OPENAI_API_KEY=sk-demo SOCTALK_FAST_MODEL=demo-playback
   SOCTALK_REASONING_MODEL=demo-playback SOCTALK_API_URL=... WORKER_TOKEN_PATH=...`
4. `seed.py run --tenant-id ... --adapter-token ...`, wait for the worker
   to drain, then `seed.py verify ...`.

## Demo box runbook (demo.soctalk.ai)

0. **Backup first — non-negotiable**:
   `ssh root@demo.soctalk.ai "kubectl exec -n soctalk-system soctalk-system-postgres-0 -- pg_dump -U soctalk_admin -d soctalk --clean --if-exists | gzip -9 > /root/soctalk-demo-backup-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"`
   and copy it off-box (kept in `~/Development/wa/soctalk-backups/`).
   **Rollback**: `gunzip -c <backup>.sql.gz | ssh root@demo.soctalk.ai "kubectl exec -i -n soctalk-system soctalk-system-postgres-0 -- psql -U soctalk_admin -d soctalk"`.
1. Scale the in-cluster worker down (its env points at real LLMs):
   `kubectl scale deploy/soctalk-runs-worker -n tenant-demo --replicas=0`.
2. SSH-tunnel the API service:
   `ssh -L 8000:$(kubectl get svc -n soctalk-system soctalk-system-api -o jsonpath='{.spec.clusterIP}'):8000 root@demo.soctalk.ai`
   (adjust svc name/port to the chart).
3. Mint adapter + worker tokens with the install signing key
   (`kubectl get secret` in soctalk-system; `mint_adapter_token`/`mint_worker_token`).
4. Start `provider.py` locally; start a LOCAL runs-worker as in the
   rehearsal (keys-free env; `SOCTALK_API_URL=http://127.0.0.1:8000`).
5. `seed.py run ... --api http://127.0.0.1:8000`; watch the worker drain;
   `seed.py verify ...`; spot-check the UI.
6. Stop the local worker + provider; scale the in-cluster worker back:
   `kubectl scale deploy/soctalk-runs-worker -n tenant-demo --replicas=1`.

Cost guarantee: the local worker's environment contains no Anthropic key
and no Modal URL — a misroute fails loudly instead of billing quietly.

## Not yet wired (deliberate)

- Memoized-close volume: `verdict_memoization_enabled` defaults off in
  install policy; events already carry `template_hash`, so enabling the
  policy later makes repeat shapes memoize for free.
- Continuous daily driver (CronJob) and multi-tenant profiles.
