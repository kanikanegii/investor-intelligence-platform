# Lesson learned: AKS pods can't reach Azure Postgres Flexible Server (firewall)

## What happened (2026-07-12)
- Deployed `invint` to AKS, pod entered `Error`/`CrashLoopBackOff`.
- Logs showed: `psycopg2.OperationalError: connection to server at "investor-db.postgres.database.azure.com" ... Connection timed out`
- Env vars were correct (Secret from `.env` wired via `kubectl set env deployment/invint --from=secret/invint-env`) — this was a **network/firewall** issue, not a credentials issue.

## Root cause
- Postgres Flexible Server (`investor-db`) has `publicNetworkAccess: Enabled` and no VNet delegation (`delegatedSubnetResourceId: null`) — i.e. it's on **public access mode**, gated by IP-based firewall rules.
- Only one firewall rule existed: a single home/office IP added earlier for local `psql`/pgAdmin testing.
- AKS nodes egress to the internet through the cluster's **Standard Load Balancer outbound public IP**, which is a completely different IP from any developer machine — so it was blocked by default.

## How we found the AKS outbound IP
```
az aks show --resource-group rg-investor-intelligence --name inv-intelligence \
  --query "networkProfile.loadBalancerProfile.effectiveOutboundIPs" -o json
# -> returns a publicIPAddresses resource id

az network public-ip show --ids <that-resource-id> --query "ipAddress" -o tsv
# -> the actual outbound IP, e.g. 172.168.16.187
```

## Fix applied
```
az postgres flexible-server firewall-rule create \
  --resource-group rg-investor-intelligence \
  --server-name investor-db \
  --name AllowAKSOutbound \
  --start-ip-address <aks-outbound-ip> \
  --end-ip-address <aks-outbound-ip>

kubectl rollout restart deployment invint
```

## Things to keep in mind for next time
- **Any new compute** (a new AKS cluster, a new node pool with different LB config, a CI runner, a serverless function) that needs to reach this Postgres server will hit the same wall — check/update firewall rules whenever the connecting environment changes.
- AKS's outbound IP **can change** if the load balancer profile is reconfigured (e.g. switching outbound type, adding more IPs for SNAT port exhaustion) — don't assume it's static forever. Re-run the `az aks show` / `az network public-ip show` check if connectivity breaks again after a cluster network change.
- Alternative, more robust long-term options instead of chasing IPs:
  - Enable **"Allow public access from any Azure service within Azure"** (`--start-ip-address 0.0.0.0` rule) — simpler but broader (any Azure tenant's resource could technically attempt to connect; access still gated by DB credentials).
  - Set up **VNet integration / private access** for the Flexible Server and peer/join it with the AKS cluster's VNet — most secure, no public firewall rules needed at all, but more setup work.
- Symptom fingerprint to recognize this class of issue quickly next time: app logs show `Connection timed out` (not "connection refused", not "role does not exist", not SSL errors) when connecting to a real hostname that's confirmed reachable/correct — that combination points at firewall/network-path blocking, not app config.
