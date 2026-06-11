# Git Push — LewisWJackson

The remote is `https://github.com/LewisWJackson/tradingview-mcp-jackson.git`.

If push fails with `403 denied to drhusam123`:

```bash
# 1. Login as LewisWJackson
gh auth login
#   → GitHub.com → HTTPS → Login with browser → authorize LewisWJackson

# 2. Verify
gh auth status

# 3. Push
cd /Users/dr.husam/tradingview-mcp-jackson
git push origin main
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
