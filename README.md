# Lose It! Nutrition Exporter

Downloads your full Lose It! data archive, extracts `food-logs.csv`, filters by a configurable date range, and saves the result as `YYYYMMDD-YYYYMMDD_nutrition.csv`.

---

## Requirements

- Python 3.10+
- A [Lose It!](https://loseit.com) account

---

## Local Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create `.env`

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

```env
LOSEIT_EMAIL=your@email.com
LOSEIT_PASSWORD=your_password
DAYS_RANGE=7
```

### 3. Run

```bash
# Last 7 days (or whatever DAYS_RANGE is set to)
python export_loseit.py

# Custom number of days
python export_loseit.py --days 14

# Specific date range
python export_loseit.py --from-date 2024-04-01 --to-date 2024-04-07

# Custom output folder
python export_loseit.py --output my_exports/
```

Output CSV is saved to `reports/YYYYMMDD-YYYYMMDD_nutrition.csv`.

---

## CLI Reference

| Flag | Description |
|------|-------------|
| `--days N` | Include the last N days (default: `DAYS_RANGE` env var or 7) |
| `--from-date YYYY-MM-DD` | Start date (use with `--to-date`) |
| `--to-date YYYY-MM-DD` | End date (use with `--from-date`) |
| `--output DIR` | Output directory (default: `reports/`) |
| `--github-summary` | Write report to `$GITHUB_STEP_SUMMARY` (used by GitHub Actions) |
| `--deploy` | Push secrets/variables to GitHub Actions from `.env` (see below) |

---

## GitHub Actions Setup

The workflow runs automatically every day at **08:00 UTC** and posts the nutrition report to the **Workflow Run Summary** page.

### Option A — Automated setup with `--deploy`

> Requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and authenticated.

1. Add `GITHUB_REPO` to your `.env`:

   ```env
   GITHUB_REPO=your-github-username/your-repo-name
   ```

2. Run:

   ```bash
   python export_loseit.py --deploy
   ```

   This sets the `LOSEIT_EMAIL` and `LOSEIT_PASSWORD` secrets and the `DAYS_RANGE` variable on the target repository.

3. Push the workflow file to GitHub:

   ```bash
   git add .github/workflows/loseit_export.yml
   git commit -m "Add Lose It! export workflow"
   git push
   ```

The scheduled run will activate on the next day.

---

### Option B — Manual setup via GitHub UI

#### Step 1 — Add Secrets

Go to your repository on GitHub → **Settings** → **Secrets and variables** → **Actions** → **Secrets** tab → **New repository secret**.

| Secret name | Value |
|-------------|-------|
| `LOSEIT_EMAIL` | Your Lose It! email address |
| `LOSEIT_PASSWORD` | Your Lose It! password |

#### Step 2 — Add Variable (optional)

**Settings** → **Secrets and variables** → **Actions** → **Variables** tab → **New repository variable**.

| Variable name | Value | Default |
|---------------|-------|---------|
| `DAYS_RANGE` | Number of days to include | `7` |

#### Step 3 — Push the workflow file

The workflow is already in `.github/workflows/loseit_export.yml`. Just push it:

```bash
git add .github/workflows/loseit_export.yml
git commit -m "Add Lose It! export workflow"
git push
```

#### Step 4 — Verify

Go to **Actions** tab in your repository. After the first scheduled run (or after triggering it manually via **Run workflow**), the nutrition report will appear in the **Workflow Run Summary**.

---

## Viewing Results

- **Local:** `reports/YYYYMMDD-YYYYMMDD_nutrition.csv`
- **GitHub Actions:** Go to **Actions** → click a run → scroll down to **Summary**

---

## Customizing the Schedule

Edit the `cron` line in `.github/workflows/loseit_export.yml`:

```yaml
- cron: '0 8 * * *'   # Every day at 08:00 UTC
```

Use [crontab.guru](https://crontab.guru) to build cron expressions.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Login failed` | Double-check `LOSEIT_EMAIL` / `LOSEIT_PASSWORD` |
| `food-logs.csv not found` | The export archive structure may have changed — open the zip manually and check file names |
| `Export did not return a zip file` | Lose It! may have changed their export URL — open a GitHub issue |
| `gh: command not found` | Install the [GitHub CLI](https://cli.github.com/) |
