# Git Push — LewisWJackson

The remote is `https://github.com/LewisWJackson/tradingview-mcp-jackson.git`.

If push fails with `403 denied to drhusam123`:

You are logged in as **drhusam123** but the repo belongs to **LewisWJackson**.

**Option A — switch to LewisWJackson (recommended):**

```bash
gh auth logout
gh auth login
# GitHub.com → HTTPS → Yes → Browser → login as LewisWJackson

gh auth status   # must show: Logged in as LewisWJackson
cd /Users/dr.husam/tradingview-mcp-jackson
git push origin main
```

**Option B — add drhusam123 as collaborator:**

On GitHub: LewisWJackson/tradingview-mcp-jackson → Settings → Collaborators → add `drhusam123` with Write access. Then:

```bash
git push origin main
```

**Option C — push to your fork:**

```bash
gh repo fork LewisWJackson/tradingview-mcp-jackson --clone=false
git remote add drhusam https://github.com/drhusam123/tradingview-mcp-jackson.git
git push drhusam main
```

Alternative (SSH):

```bash
git remote set-url origin git@github.com:LewisWJackson/tradingview-mcp-jackson.git
ssh -T git@github.com   # must show LewisWJackson
git push origin main
```

One-liner after auth:

```bash
npm run egx:go:live    # includes push attempt
```
