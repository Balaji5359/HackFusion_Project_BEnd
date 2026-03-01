# UI Runbook

## 1. Install UI dependencies
```powershell
pip install -r ui/requirements.txt
```

## 2. Import provided hackathon dataset (Medicines only)
```powershell
python infra/import_hackfusion_dataset.py
```

## 3. Create observability table
```powershell
python infra/setup_observability_table.py
```

## 4. Run UI
```powershell
streamlit run ui/app.py
```

Optional admin password override (default is `admin@123`):
```powershell
$env:UI_ADMIN_PASSWORD="your-secure-password"
streamlit run ui/app.py
```

## 5. What this UI shows
- User Chat: invokes SupervisorAgent with traces.
- Explainability chain: Intent -> Safety -> Action.
- Trace map graph of agent communication.
- Suggestion score (0-100) for proposed order confidence.
- Admin Dashboard: success rate, latency, approval ratio, top medicines, recent runs.
- Admin login gate for `Admin Dashboard` and `System Status`.
- Trace timeline table (step-by-step) for each run.
- System Status: checks all agent state files.
