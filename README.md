# AI-Powered Investor Intelligence Platform

<img width="2560" height="1638" alt="AI-Powered Investor Intelligence Platform dashboard" src="docs/dashboard-screenshot.png" />

This repository contains the Python backend for an AI-powered Investor Intelligence Platform, including document ingestion, semantic search, KPI extraction, Azure AI Search integration, Azure OpenAI integration, and PostgreSQL-based KPI storage.

## Prerequisites

* Python 3.12+
* UV Package Manager

## Setup

### 1. Install UV

#### Windows

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

#### macOS/Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify installation:

```bash
uv --version
```

---

### 2. Create Virtual Environment

```bash
uv venv
```

---

### 3. Activate Virtual Environment

#### Windows

```bash
.venv\Scripts\activate
```

#### macOS/Linux

```bash
source .venv/bin/activate
```

---

### 4. Install Dependencies

```bash
uv pip install -r requirements.txt
```

---

### 5. Configure Environment Variables

Create a `.env` file and configure all required environment variables before running the application.

---

### 6. Run the Application

```bash
python main.py
```

---

## Project Features

* Annual Report Upload & Processing
* KPI Extraction using Azure OpenAI
* Azure AI Search Integration
* Semantic Search & Retrieval
* RAG-based Chatbot
* PostgreSQL KPI Storage
* Investor Insights Dashboard
* Production-Grade Modular Architecture

---

## Technology Stack

### Backend

* FastAPI
* Python 3.12

### AI Services

* Azure OpenAI
* Azure AI Search

### Database

* Azure PostgreSQL

### Deployment

* Docker
* Azure Container Registry (ACR)
* Azure Kubernetes Service (AKS)

### Package Management

* UV

---

## Deploying to AKS

### Option A: Automated (CI/CD)

Pushing to the `cicd-setup` branch triggers [.github/workflows/deploy.yaml](.github/workflows/deploy.yaml), which:

1. Builds the Docker image from this repo.
2. Pushes it to Azure Container Registry (ACR).
3. Applies `k8s/deployment.yaml` and `k8s/service.yaml` to AKS.
4. Restarts the deployment and waits for the rollout to finish.

To deploy this way:

```bash
git add .
git commit -m "your change"
git push origin <your-branch>:cicd-setup
```

This requires the following GitHub Actions secrets to already be configured on the repo (`AZURE_CREDENTIALS`, `ACR_NAME`, `ACR_LOGIN_SERVER`, `ACR_USERNAME`, `ACR_PASSWORD`, `AKS_RESOURCE_GROUP`, `AKS_CLUSTER_NAME`, plus the app's Azure OpenAI/Search/Postgres secrets) — see [CICD_Deployment_Guide.md](CICD_Deployment_Guide.md) for what each one is for.

### Option B: Manual

```bash
# 1. Build the image
docker build -t <acr-login-server>/invint:latest .

# 2. Log in and push to ACR
az acr login --name <acr-name>
docker push <acr-login-server>/invint:latest

# 3. Point kubectl at the AKS cluster
az aks get-credentials --resource-group <resource-group> --name <cluster-name>

# 4. Deploy
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 5. Roll out the new image
kubectl rollout restart deployment/invint
kubectl rollout status deployment/invint
```

`k8s/deployment.yaml` pulls app config/secrets from a Kubernetes secret named `invint-secrets` — create it before the first deploy:

```bash
kubectl create secret generic invint-secrets \
  --from-literal=AZURE_OPENAI_ENDPOINT="<value>" \
  --from-literal=AZURE_OPENAI_API_KEY="<value>" \
  --from-literal=AZURE_SEARCH_ENDPOINT="<value>" \
  --from-literal=AZURE_SEARCH_API_KEY="<value>" \
  --from-literal=POSTGRES_HOST="<value>" \
  --from-literal=POSTGRES_USER="<value>" \
  --from-literal=POSTGRES_PASSWORD="<value>"
  # ...and any other vars from .env.example
```

For a full field-by-field breakdown of `deployment.yaml`, `service.yaml`, and the workflow, see [CICD_Deployment_Guide.md](CICD_Deployment_Guide.md).

---

## Notes

* Ensure all Azure resources are configured before running the application.
* Verify that PostgreSQL firewall rules allow access from the application.
* Store secrets in environment variables and never commit `.env` files to source control.
* For production deployments, use Azure Key Vault or Kubernetes Secrets for secret management.
