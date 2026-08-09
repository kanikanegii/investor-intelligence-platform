# AKS CI/CD Deployment Reference Guide

This document explains every field used in the following files:

```text
k8s/deployment.yaml
k8s/service.yaml
k8s/ingress.yaml
k8s/cluster-issuer.yaml
.github/workflows/deploy.yaml
```

The goal is to understand not only what each field does, but also why we are using it in our Investor Intelligence Platform project.

---

# deployment.yaml

## Complete File

```yaml
apiVersion: apps/v1
kind: Deployment

metadata:
  name: invint

spec:
  replicas: 1

  selector:
    matchLabels:
      app: invint

  template:
    metadata:
      labels:
        app: invint

    spec:
      containers:
      - name: invint

        image: invintelligence.azurecr.io/invint:latest

        imagePullPolicy: Always

        envFrom:
        - secretRef:
            name: invint-secrets

        ports:
        - containerPort: 8000
```

---

## apiVersion

```yaml
apiVersion: apps/v1
```

This tells Kubernetes which API group should process this resource. Deployments belong to the `apps/v1` API group. Whenever Kubernetes reads this file, it knows that this resource should be handled by the Deployment controller.

---

## kind

```yaml
kind: Deployment
```

This tells Kubernetes what resource to create.

In Kubernetes, everything is represented as a resource.

Examples:

```text
Deployment
Service
Secret
ConfigMap
Ingress
```

In our case we want Kubernetes to create and manage application pods, therefore we use a Deployment resource.

---

## metadata

```yaml
metadata:
  name: invint
```

This is the unique name of the deployment inside the cluster.

We use this name later when checking deployment status, restarting deployments, viewing logs, and troubleshooting.

Examples:

```bash
kubectl get deployment invint
kubectl rollout restart deployment/invint
kubectl describe deployment invint
```

Think of this as the identifier for our application deployment.

---

## replicas

```yaml
replicas: 1
```

This defines how many copies of the application should be running.

For our project we use:

```yaml
replicas: 1
```

to reduce Azure costs while demonstrating the deployment process.

In production systems this is usually increased.

Example:

```yaml
replicas: 3
```

This would create three identical application pods.

If one pod crashes, the remaining pods continue serving users.

---

## selector

```yaml
selector:
  matchLabels:
    app: invint
```

The deployment needs a way to identify which pods belong to it.

The selector tells Kubernetes:

```text
Manage all pods having app=invint
```

This creates the relationship between the deployment and its pods.

The selector must match the labels defined inside the pod template.

---

## template

```yaml
template:
```

The template section acts as the blueprint used to create pods.

Whenever Kubernetes needs to create a new pod, it uses the configuration defined inside this section.

Think of this as a manufacturing template.

Every pod created by the deployment will follow this template.

---

## labels

```yaml
labels:
  app: invint
```

Labels are tags attached to Kubernetes resources.

Here we are assigning:

```text
app=invint
```

to all application pods.

These labels are later used by:

```text
Deployments
Services
Monitoring Tools
Ingress Controllers
```

to identify resources.

---

## container name

```yaml
name: invint
```

This defines the container name inside the pod.

This name is mainly used for:

```text
Logging
Debugging
Monitoring
Container Identification
```

When multiple containers exist inside a pod, this name becomes important.

---

## image

```yaml
image: invintelligence.azurecr.io/invint:latest
```

This tells Kubernetes where the application image is stored.

Breakdown:

```text
Registry    : invintelligence.azurecr.io
Repository  : invint
Tag         : latest
```

When a pod starts, AKS downloads this image from Azure Container Registry.

This image contains:

```text
Application Code
Python Runtime
Dependencies
Libraries
```

Everything required to run the application.

---

## containerPort

```yaml
containerPort: 8000
```

This tells Kubernetes that the FastAPI application is listening on port 8000 inside the container.

This port is internal to the container.

Users will never directly access this port.

Instead:

```text
Load Balancer
↓
Service
↓
Container Port 8000
```

---

## imagePullPolicy

```yaml
imagePullPolicy: Always
```

This instructs Kubernetes to always check ACR for the latest image before starting a pod.

This is useful during CI/CD because every deployment automatically pulls the newest image.

Without this, Kubernetes may reuse an older cached image.

---

## envFrom

```yaml
envFrom:
- secretRef:
    name: invint-secrets
```

This bulk-imports **every key** inside the `invint-secrets` Kubernetes Secret
as an environment variable in the container — one field, instead of listing
each variable individually with `env:` + `valueFrom.secretKeyRef` per key.

The `invint-secrets` Secret itself is created by the pipeline's own
**Create Kubernetes Secrets** step (see below) — this file only references
it by name, it doesn't create it. If that Secret doesn't exist yet, or was
created under a different name, the pod will start with none of these
environment variables set and crash on startup (the app reads several of
them, like `AZURE_TENANT_ID`, via `os.environ[...]` rather than `.get()`,
so a missing one is a hard crash, not a silent default).

**Real incident this caught us on:** at one point the actual name in this
Secret didn't match what was live in the cluster (`invint-env` existed
instead of `invint-secrets`, created by hand during earlier firewall
debugging rather than by this pipeline) — the app kept running on stale,
manually-patched env vars instead of ever picking up what CI/CD intended.
`envFrom`/`secretRef` only works correctly when the name here and the name
the pipeline creates actually agree — see the **Troubleshooting** section
at the bottom of this doc for the full story.

---

# service.yaml

## Complete File

```yaml
apiVersion: v1
kind: Service

metadata:
  name: invint

spec:
  selector:
    app: invint

  type: ClusterIP

  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
```

---

## apiVersion

```yaml
apiVersion: v1
```

Services belong to the Kubernetes Core API group, which uses `v1`.

This tells Kubernetes which API should handle the Service resource.

---

## kind

```yaml
kind: Service
```

Creates a networking layer for our application.

Without a Service:

```text
Pods Exist
Application Exists
Users Cannot Access It
```

The Service acts as the bridge between users and application pods.

---

## metadata

```yaml
metadata:
  name: invint
```

This becomes the name of the Service.

Useful commands:

```bash
kubectl get svc
kubectl describe svc invint
```

---

## selector

```yaml
selector:
  app: invint
```

The Service must know which pods should receive traffic.

This selector tells Kubernetes:

```text
Forward traffic to pods having app=invint
```

Since our deployment creates pods with:

```yaml
labels:
  app: invint
```

the Service can automatically discover them.

---

## type

```yaml
type: ClusterIP
```

This used to be `type: LoadBalancer` (Azure would create its own public IP
+ Azure Load Balancer directly in front of this Service, same idea as
below). That worked, but only for plain HTTP, on a raw IP, with no
hostname-based routing and no TLS.

**Why it changed to `ClusterIP` — the tradeoff:**

| | `type: LoadBalancer` (old) | `type: ClusterIP` + Ingress (current) |
|---|---|---|
| Public IPs needed | One per Service | One, total, shared by every Service routed through the Ingress controller |
| TLS / HTTPS | Not handled — plaintext only | Centralized, automatic via `cert-manager` (see `k8s/cluster-issuer.yaml`) |
| Hostname routing | None — one IP, one app | Route multiple domains/paths through the same IP |
| Cost as more services are added | Scales linearly (new LB + IP each time) | Flat — new services are just new `Ingress` rules |

`ClusterIP` makes this Service **internal-only** — reachable only from
inside the cluster, not directly from the internet. The only way in from
outside now is through `k8s/ingress.yaml`, routed via the `ingress-nginx`
controller (which is what actually holds the public IP now).

**Why this matters beyond cost:** if this were still `type: LoadBalancer`
*alongside* the Ingress setup, the app would be reachable two ways at
once — once through the Ingress (HTTPS, valid cert, hostname-checked), and
once through the old raw LoadBalancer IP (plain HTTP, no cert, no checks).
That second path would be a live, unencrypted bypass of everything the
Ingress/TLS setup exists to enforce — bearer tokens sent through it would
travel in plaintext. `ClusterIP` closes that path entirely.

---

## protocol

```yaml
protocol: TCP
```

Specifies the network protocol used for communication.

Web applications typically use:

```text
TCP
```

because HTTP and HTTPS are built on top of TCP.

---

## port

```yaml
port: 80
```

This is the public port exposed to users.

Users access:

```text
http://<public-ip>
```

which automatically uses port 80.

---

## targetPort

```yaml
targetPort: 8000
```

The Service forwards incoming traffic to the FastAPI application running on container port 8000.

Traffic flow:

```text
User
 ↓
Public IP
 ↓
Port 80
 ↓
Service
 ↓
Port 8000
 ↓
FastAPI Application
```

---

# cluster-issuer.yaml

## Complete File

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer

metadata:
  name: letsencrypt-prod

spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: kanikanegi2910@gmail.com

    privateKeySecretRef:
      name: letsencrypt-prod-account-key

    solvers:
    - http01:
        ingress:
          ingressClassName: nginx
```

This tells **cert-manager** (a separate controller installed on the
cluster, not something in this repo) how to actually obtain a real TLS
certificate — specifically, from Let's Encrypt's production ACME server.

`ClusterIssuer` is cluster-wide (as opposed to `Issuer`, which would be
scoped to one namespace) — any `Ingress` in the cluster can reference this
one issuer by name.

**`solvers.http01`**: this is *how* cert-manager proves domain ownership to
Let's Encrypt — it temporarily serves a challenge token at
`http://<your-domain>/.well-known/acme-challenge/...` through the
`ingress-nginx` ingress class, and Let's Encrypt fetches that token to
confirm you actually control the domain before issuing a cert. This all
happens automatically; nothing manual to do per certificate.

**Rate limits, and why there's also a staging version:** Let's Encrypt's
production server limits ~5 duplicate certificates per domain per week. A
second file, `k8s/cluster-issuer-staging.yaml`, points at Let's Encrypt's
**staging** ACME server instead — same mechanics, but certs it issues
aren't trusted by browsers. It exists purely so Ingress/TLS config can be
iterated on and tested without burning the production rate limit, and is
deliberately **not** referenced by the live `Ingress` or applied by CI/CD —
it's a manual tool, swap `k8s/ingress.yaml`'s `cert-manager.io/cluster-issuer`
annotation to `letsencrypt-staging` temporarily if you need to test changes.

---

# ingress.yaml

## Complete File

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress

metadata:
  name: invint
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod

spec:
  ingressClassName: nginx

  tls:
  - hosts:
    - investor-ai-platform.site
    secretName: invint-tls

  rules:
  - host: investor-ai-platform.site
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: invint
            port:
              number: 80
```

This is the piece that actually makes `service.yaml`'s `type: ClusterIP`
reachable from the internet at all.

**`cert-manager.io/cluster-issuer: letsencrypt-prod`**: this annotation is
what wires this specific `Ingress` to the `ClusterIssuer` above — cert-manager
watches for `Ingress` objects with this annotation and automatically
requests/renews a certificate for whatever hosts are listed under `tls`.

**`ingressClassName: nginx`**: tells Kubernetes which installed Ingress
Controller should handle this object. `ingress-nginx` is the controller
installed on this cluster (a separate install, not part of this repo's
manifests — see the Troubleshooting section for how it was set up); its
own Service is `type: LoadBalancer` and holds the cluster's one public IP.
Every app's `Ingress` — today just this one — routes through that single
controller and IP.

**`tls.hosts` / `tls.secretName`**: requests a certificate for
`investor-ai-platform.site` and tells cert-manager to store the resulting
cert+key in a Secret named `invint-tls` (created automatically — nothing to
pre-create).

**`rules.host`**: this Ingress only matches requests where the `Host:`
header is `investor-ai-platform.site`. This is what host-based routing
actually means in practice — a raw `LoadBalancer` Service has no concept of
this at all, it just forwards every request on the port regardless of what
hostname was requested.

**`rules.http.paths`**: path `/` with `pathType: Prefix` matches everything
— all traffic for this host goes to the `invint` Service on port `80`
(which itself forwards to container port `8000`, per `service.yaml`).

---

# deploy.yaml

## Complete File

```yaml
name: Build and Deploy to AKS

on:
  push:
    branches:
      - main

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:

      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Azure Login
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Login to ACR
        run: |
          az acr login --name ${{ secrets.ACR_NAME }}

      - name: Docker Login to ACR
        run: |
          docker login ${{ secrets.ACR_LOGIN_SERVER }} \
            -u ${{ secrets.ACR_USERNAME }} \
            -p ${{ secrets.ACR_PASSWORD }}

      - name: Build Docker Image
        run: |
          docker build \
            -t ${{ secrets.ACR_LOGIN_SERVER }}/invint:latest .

      - name: Push Docker Image
        run: |
          docker push \
            ${{ secrets.ACR_LOGIN_SERVER }}/invint:latest

      - name: Get AKS Credentials
        run: |
          az aks get-credentials \
            --resource-group ${{ secrets.AKS_RESOURCE_GROUP }} \
            --name ${{ secrets.AKS_CLUSTER_NAME }} \
            --overwrite-existing

      - name: Create Kubernetes Secrets
        run: |
          kubectl create secret generic invint-secrets \
            --from-literal=AZURE_OPENAI_ENDPOINT="${{ secrets.AZURE_OPENAI_ENDPOINT }}" \
            --from-literal=AZURE_OPENAI_CHAT_ENDPOINT="${{ secrets.AZURE_OPENAI_CHAT_ENDPOINT }}" \
            --from-literal=AZURE_OPENAI_API_KEY="${{ secrets.AZURE_OPENAI_API_KEY }}" \
            --from-literal=AZURE_OPENAI_API_EMBEDDING_VERSION="${{ secrets.AZURE_OPENAI_API_EMBEDDING_VERSION }}" \
            --from-literal=AZURE_OPENAI_API_VERSION="${{ secrets.AZURE_OPENAI_API_VERSION }}" \
            --from-literal=AZURE_SEARCH_ENDPOINT="${{ secrets.AZURE_SEARCH_ENDPOINT }}" \
            --from-literal=AZURE_SEARCH_API_KEY="${{ secrets.AZURE_SEARCH_API_KEY }}" \
            --from-literal=AZURE_SEARCH_INDEX_NAME="${{ secrets.AZURE_SEARCH_INDEX_NAME }}" \
            --from-literal=AZURE_OPENAI_EMBEDDING_DEPLOYMENT="${{ secrets.AZURE_OPENAI_EMBEDDING_DEPLOYMENT }}" \
            --from-literal=AZURE_OPENAI_CHAT_DEPLOYMENT="${{ secrets.AZURE_OPENAI_CHAT_DEPLOYMENT }}" \
            --from-literal=AZURE_OPENAI_JUDGE_DEPLOYMENT="${{ secrets.AZURE_OPENAI_JUDGE_DEPLOYMENT }}" \
            --from-literal=AZURE_TENANT_ID="${{ secrets.AZURE_TENANT_ID }}" \
            --from-literal=AZURE_CLIENT_ID="${{ secrets.AZURE_CLIENT_ID }}" \
            --from-literal=POSTGRES_HOST="${{ secrets.POSTGRES_HOST }}" \
            --from-literal=POSTGRES_PORT="${{ secrets.POSTGRES_PORT }}" \
            --from-literal=POSTGRES_DATABASE="${{ secrets.POSTGRES_DATABASE }}" \
            --from-literal=POSTGRES_USER="${{ secrets.POSTGRES_USER }}" \
            --from-literal=POSTGRES_PASSWORD="${{ secrets.POSTGRES_PASSWORD }}" \
            --dry-run=client -o yaml | kubectl apply -f -

      - name: Deploy Application
        run: |
          kubectl apply -f k8s/deployment.yaml
          kubectl apply -f k8s/service.yaml
          kubectl apply -f k8s/cluster-issuer.yaml
          kubectl apply -f k8s/ingress.yaml

      - name: Restart Deployment
        run: |
          kubectl rollout restart deployment/invint

      - name: Verify Rollout
        run: |
          kubectl rollout status deployment/invint
```

---

## Workflow Name

```yaml
name: Build and Deploy to AKS
```

The name displayed inside GitHub Actions.

This helps identify the workflow in the Actions dashboard.

---

## Trigger

```yaml
on:
  push:
    branches:
      - main
```

This tells GitHub when to execute the pipeline. Whenever code is pushed to
this branch, the workflow automatically starts.

**This used to say `cicd-setup` instead of `main` — the tradeoff behind
changing it:** `cicd-setup` (both the local and `origin` copies) turned out
to be an old branch with unrelated, diverged history containing commits
literally named `add secret` / `removed secret` — not something to keep
pushing clean code onto, and not safely fast-forwardable either. Two
options existed:

- **Force-push clean history over `cicd-setup`** — would have worked, but
  force-push is a destructive operation, and doing it silently onto a
  branch whose name suggested other tooling might still reference it felt
  like the wrong default.
- **Retarget the trigger to `main` instead (chosen)** — `main` was already
  the branch receiving every clean push all along. Zero destructive git
  operations needed, and the legacy `cicd-setup` branch never has to be
  touched again.

---

## runs-on

```yaml
runs-on: ubuntu-latest
```

GitHub creates a temporary Ubuntu virtual machine to execute all pipeline steps.

Think of this as a temporary build server.

---

## Checkout Repository

```yaml
uses: actions/checkout@v4
```

Downloads the source code into the GitHub runner.

Without this step:

```text
Dockerfile Not Available
Source Code Not Available
Build Fails
```

---

## Azure Login

```yaml
uses: azure/login@v2
```

Authenticates GitHub Actions with Azure.

This step allows the pipeline to:

```text
Access AKS
Access ACR
Execute Azure CLI Commands
```

---

## Login to ACR

```yaml
az acr login
```

Authenticates Docker against Azure Container Registry.

Without this step:

```text
Docker Push Fails
Unauthorized Error
```

---

## Docker Login to ACR

```yaml
docker login ${{ secrets.ACR_LOGIN_SERVER }} \
  -u ${{ secrets.ACR_USERNAME }} \
  -p ${{ secrets.ACR_PASSWORD }}
```

A second, separate login — this one authenticates the `docker` CLI
specifically (as opposed to `az acr login` above, which authenticates the
`az` CLI's own session). Uses ACR's **admin username/password** credentials
rather than the Azure service principal from the `Azure Login` step.

This is a known, deliberately temporary tradeoff, not the ideal end state —
see `TODO_acr_rbac.md`: ACR currently has admin credentials enabled, when
the more secure long-term approach is an `AcrPush`-scoped role assignment
for this pipeline's service principal instead, with the admin account
disabled. Not yet migrated — flagged as a separate, deliberately deferred
task since changing it risks breaking image pushes if the role assignment
isn't verified working before the admin account is disabled.

---

## Build Docker Image

```yaml
docker build
```

Creates a Docker image from:

```text
Dockerfile
Source Code
Requirements
Dependencies
```

This produces a deployable container image.

---

## Push Docker Image

```yaml
docker push
```

Uploads the Docker image to Azure Container Registry.

After this step:

```text
AKS Can Pull The Image
```

---

## Get AKS Credentials

```yaml
az aks get-credentials
```

Connects GitHub Actions to the AKS cluster.

This command automatically configures kubectl to communicate with the cluster.

Without this step:

```text
kubectl Cannot Access AKS
```

---

## Create Kubernetes Secrets

```yaml
kubectl create secret generic invint-secrets \
  --from-literal=AZURE_OPENAI_ENDPOINT="${{ secrets.AZURE_OPENAI_ENDPOINT }}" \
  ...
  --dry-run=client -o yaml | kubectl apply -f -
```

Builds the `invint-secrets` Kubernetes Secret that `deployment.yaml`'s
`envFrom` bulk-imports — one `--from-literal=KEY="value"` per environment
variable the app needs, sourced from GitHub Actions repository secrets.

**`--dry-run=client -o yaml | kubectl apply -f -`**: this pattern (instead
of just running `kubectl create secret ...` directly) is what makes this
step **idempotent** — `kubectl create` alone would fail with "already
exists" on every run after the first. Generating the YAML client-side with
`--dry-run=client` and piping it into `kubectl apply` instead means the
Secret gets created on the first run and **updated in place** on every run
after, which is exactly the "safe to run every deploy" behavior CI/CD
needs.

**Why `--from-literal` here specifically matters, not just as a style
choice:** `--from-literal=KEY="value"` takes the value as a shell
argument — the double quotes are shell syntax for the argument boundary,
they are never stored as part of the value itself. This is different from
`--from-env-file=<file>`, which reads `KEY=value` lines directly and does
**not** strip any quote characters written inside the file — if the source
`.env`-style file has values like `KEY='https://...'` (quotes as literal
file content, common in bash-style `.env` files), `--from-env-file` stores
those quote characters as part of the secret's actual value. This exact
distinction caused a real production outage — see **Troubleshooting**
below.

**Which secrets are required vs. safe to leave unset:** every
`--from-literal` line here needs a matching GitHub Actions repository
secret of the same name, with the value copied from the project's local
`.env` (minus any surrounding quotes — see above). Two exceptions —
`AZURE_OPENAI_API_EMBEDDING_VERSION` and `AZURE_OPENAI_API_VERSION` — are
leftover from an earlier, date-versioned Azure OpenAI API pattern; current
code (`llm/azure_openai.py`) uses the newer versionless endpoint and never
reads either one, so they're safe to leave unset (they'll just resolve to
empty strings that nothing consumes).

---

## Deploy Application

```yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/cluster-issuer.yaml
kubectl apply -f k8s/ingress.yaml
```

Creates each resource if it doesn't exist yet, updates it in place if it
does — same idempotent `apply` pattern as the secrets step above. This is
why manual imperative commands like `kubectl create deployment` /
`kubectl expose deployment` are never needed here.

The `cluster-issuer.yaml`/`ingress.yaml` lines were added after the
Ingress/TLS setup was first done by hand (`kubectl apply` run manually, one
time, from a local terminal) — folding them into the pipeline here means
every future deploy keeps TLS/routing config in sync automatically, instead
of that manual step being something a human has to remember to redo.
Deliberately **not** included here: `k8s/cluster-issuer-staging.yaml` — see
the `cluster-issuer.yaml` section above for why that one stays manual.

---

## Restart Deployment

```yaml
kubectl rollout restart deployment/invint
```

Forces Kubernetes to recreate pods using the latest image from ACR.

This ensures newly pushed images are picked up immediately.

---

## Verify Rollout

```yaml
kubectl rollout status deployment/invint
```

Waits until deployment completes successfully.

This acts as a health check for the deployment process.

If the deployment fails, the GitHub Action also fails.

---

# Troubleshooting: diagnosing a "works locally, broken in production" issue

A real incident, kept here because the diagnostic *method* generalizes well
beyond this one bug.

**Symptom:** the production dashboard didn't show the sign-in UI it has
locally, and the chat endpoint returned:
```text
No connection adapters were found for "'https://ai-search-investor-intelligence.search.windows.net'/indexes(...)"
```

## The general pattern

Whenever something works locally but not in the cluster, check things in
this order — because "what Kubernetes *thinks* is configured" and "what the
running process *actually* sees" can silently disagree, and that
disagreement is usually the bug:

**1. What does Kubernetes think is configured?**
```bash
kubectl get secret invint-secrets -o jsonpath='{.data.AZURE_SEARCH_ENDPOINT}' | base64 -d
```
Pulls one field out of a Secret object. Kubernetes Secrets are stored
**base64-encoded**, not encrypted — `| base64 -d` decodes it back to the
real string. This returned `NotFound`, which was the first clue: the
Secret this deployment expects didn't exist at all.

**2. What actually exists, and how is it wired?**
```bash
kubectl get secrets
kubectl get deployment invint -o jsonpath='{.spec.template.spec.containers[0].envFrom}'
kubectl get deployment invint -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.valueFrom.secretKeyRef.name}/{.valueFrom.secretKeyRef.key}{"\n"}{end}'
```
`kubectl get secrets` lists every Secret in the namespace by name — this
found `invint-env` instead of the expected `invint-secrets`. The
`envFrom` check came back empty, and the `range` jsonpath loop (iterates
over an array, printing something per entry) revealed the env vars were
wired individually, by hand, pointing at `invint-env` — evidence of a
manual `kubectl set env ...` patch that had drifted away from what this
repo's `deployment.yaml` actually declares.

**3. Decode the value and check its raw bytes, not just how it prints**
```bash
kubectl get secret invint-env -o jsonpath='{.data.AZURE_SEARCH_ENDPOINT}' | base64 -d | xxd | head -5
```
Piping through `xxd` (hex dump) instead of trusting the plain-text decode
is what proved the value had **literal single-quote characters** (byte
`0x27`) at both ends — `'https://...'` — not just something that looked
quoted in terminal output.

**4. Check what the *running pod* actually has, not just what's declared**
```bash
kubectl exec deployment/invint -- printenv | grep -i "AZURE_OPENAI\|AZURE_SEARCH"
```
`kubectl exec` runs a command **inside** the live container. This is the
step that matters most — it's the difference between reading Kubernetes
config and observing the real process. It's what revealed
`AZURE_OPENAI_API_KEY` (the name the code actually reads) didn't exist at
all — only a mistyped `AZURE_OPEN_AI_KEY` did.

**5. Check which image is actually deployed**
```bash
kubectl get deployment invint -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl get pod -o jsonpath='{.items[0].status.startTime}'
```
Returned `invintelligence.azurecr.io/invint:v1` — not `:latest` as this
repo's `deployment.yaml` says. This is what proved the deployed code
predated the whole session's worth of local changes: the CI/CD pipeline
had never actually run successfully before, every prior deploy was manual.

## Root cause and the fix

Traced back to the local `.env` file's bash-style quoting
(`AZURE_SEARCH_ENDPOINT='https://...'`) — `python-dotenv` correctly strips
those quotes on load (why it always worked locally), but the `invint-env`
Secret had been created via something like `--from-env-file=.env`, which
splits on `=` and keeps everything after it **verbatim**, quote characters
included.

Two fixes were possible:

- **Hand-patch `invint-env`'s values and manually rebuild/push a `:latest`
  image.** Would have resolved the symptom, but left the CI/CD pipeline as
  unproven as before, with the same drift able to silently recur next time
  someone deploys by hand.
- **Actually trigger the pipeline (chosen).** Rebuilds the image from
  current code *and* recreates `invint-secrets` cleanly via
  `--from-literal` (which, as covered in the Create Kubernetes Secrets
  section above, doesn't have the quote-corruption problem
  `--from-env-file` does) — fixing both root causes in the same action that
  finally exercises the pipeline for real.

Two more gaps were found while preparing to actually trigger it — a
misspelled `AZURE_CRENDENTIALS` GitHub secret (workflow reads
`secrets.AZURE_CREDENTIALS`; exact-name mismatches resolve to empty, so
`Azure Login` had likely always failed silently whenever this pipeline
tried to run) and two secrets missing outright
(`AZURE_TENANT_ID`/`AZURE_CLIENT_ID`, added to the app this session for
MSAL — both read via `os.environ[...]`, so a missing value crashes the app
at startup, not just one request). Both are now fixed; see the Trigger and
Create Kubernetes Secrets sections above for the details.
