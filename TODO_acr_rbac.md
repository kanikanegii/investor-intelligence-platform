# TODO: Switch ACR auth from admin credentials to RBAC (AcrPull)

## Current state (2026-07-12)
- ACR `invintelligence` has `adminUserEnabled: true`.
- We're authenticating with the registry's admin username/password (from `az acr credential show`) instead of an AAD-based role assignment.
- Reason: account has `Owner` at the subscription level, but Owner's `Actions: ["*"]` does not include ACR **data-plane** actions (pulling/listing repositories) — those require an explicit `DataActions` grant via a role like `AcrPull`/`AcrPush`, or use of the registry's admin credentials.

## Why move off admin credentials eventually
- Admin credentials are a single shared secret (not tied to an individual identity) — no per-user audit trail, can't be scoped, harder to rotate safely across a team.
- Microsoft recommends disabling the admin account and using AAD/RBAC-based auth for production registries.

## Steps to switch to RBAC-based auth

1. **Get the registry resource ID** (already known):
   ```
   /subscriptions/225249dd-740e-4cce-a333-89863213d7f0/resourceGroups/rg-investor-intelligence/providers/Microsoft.ContainerRegistry/registries/invintelligence
   ```

2. **Create the role assignment** for the account that needs pull access (replace `<principal>` with the exact UPN, e.g. `kanikanegi2910_gmail.com#EXT#@kanikanegi2910gmail.onmicrosoft.com` for a guest/B2B account, or an app/service principal's object ID for CI/CD):
   ```
   az role assignment create \
     --assignee "<principal>" \
     --role AcrPull \
     --scope /subscriptions/225249dd-740e-4cce-a333-89863213d7f0/resourceGroups/rg-investor-intelligence/providers/Microsoft.ContainerRegistry/registries/invintelligence
   ```
   - Use `AcrPush` instead of `AcrPull` if the account also needs to push images (e.g. CI/CD pipeline).

3. **Verify the assignment landed**:
   ```
   az role assignment list \
     --scope /subscriptions/225249dd-740e-4cce-a333-89863213d7f0/resourceGroups/rg-investor-intelligence/providers/Microsoft.ContainerRegistry/registries/invintelligence \
     -o table
   ```

4. **Test token-based login** (no username/password needed):
   ```
   az acr login --name invintelligence
   az acr repository list --name invintelligence --output table
   ```
   RBAC propagation can take a few minutes — retry if it fails immediately after step 2.

5. **Once RBAC access is confirmed working, disable the admin account**:
   ```
   az acr update --name invintelligence --admin-enabled false
   ```

6. **Rotate out any admin credentials** used in scripts/CI (`.env`, deployment docs, secrets stores) — replace with `az acr login` (interactive) or a service principal + `AcrPull`/`AcrPush` role for automated pipelines.

## Notes
- For CI/CD (GitHub Actions, etc.), prefer a **service principal** or **managed identity** with `AcrPull`/`AcrPush` scoped to just this registry, rather than a personal account's role assignment.
- `deployment-Document.md` and `CICD_Deployment_Guide.md` may reference admin credentials — update those once the switch is made.
