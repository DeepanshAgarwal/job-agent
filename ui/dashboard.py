"""
ui/dashboard.py — Streamlit dashboard for the Job Application Agent.

Run with: streamlit run ui/dashboard.py

Provides:
  - Today's stats (scraped, applied, failed)
  - Table of recent applications with filtering
  - Manual "Run Agent" trigger button (dry-run safe)
"""

import subprocess
import sys
from pathlib import Path

# Add repo root to PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from agent.tracker.database import Database

st.set_page_config(
    page_title="Job Agent Dashboard",
    page_icon="🤖",
    layout="wide",
)

st.title("🤖 Job Application Agent Dashboard")

db = Database()
db.init_db()

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("Controls")

if st.sidebar.button("▶ Run Agent (Dry Run)", use_container_width=True):
    with st.spinner("Running agent in dry-run mode…"):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "agent/main.py", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
    if result.returncode == 0:
        st.sidebar.success("Dry run complete!")
    else:
        st.sidebar.error(f"Error: {result.stderr[:300]}")

if st.sidebar.button("▶ Run Agent (Full)", use_container_width=True):
    st.sidebar.warning(
        "Full run will actually submit applications. "
        "Make sure you've reviewed the shortlist preview."
    )

st.sidebar.markdown("---")
st.sidebar.markdown("📖 [Documentation](docs/setup-guide.md)")
st.sidebar.markdown("⚙️ [Preferences](config/preferences.yaml)")

# ── Stats ─────────────────────────────────────────────────────────────────────
stats = db.get_stats()
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Total Applied", stats.get("total_applied", 0))

with col2:
    st.metric("Applied", stats.get("by_status", {}).get("applied", 0))

with col3:
    st.metric("Failed", stats.get("by_status", {}).get("failed", 0))

st.markdown("---")

# ── Filters ───────────────────────────────────────────────────────────────────
st.subheader("Recent Applications")

col_f1, col_f2 = st.columns(2)
with col_f1:
    platform_filter = st.selectbox(
        "Platform",
        ["All"] + list(stats.get("by_platform", {}).keys()),
    )
with col_f2:
    status_filter = st.selectbox(
        "Status",
        ["All"] + list(stats.get("by_status", {}).keys()),
    )

# ── Application table ─────────────────────────────────────────────────────────
applications = db.get_recent_applications(limit=100)

if platform_filter != "All":
    applications = [a for a in applications if a.get("source") == platform_filter]
if status_filter != "All":
    applications = [a for a in applications if a.get("status") == status_filter]

if applications:
    import pandas as pd

    df = pd.DataFrame(applications)
    display_cols = ["applied_at", "company", "role", "source", "match_score", "status", "notes"]
    display_cols = [c for c in display_cols if c in df.columns]
    df = df[display_cols].rename(columns={
        "applied_at": "Applied At",
        "company": "Company",
        "role": "Role",
        "source": "Platform",
        "match_score": "Score",
        "status": "Status",
        "notes": "Notes",
    })
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No applications recorded yet. Run the agent to get started!")

# ── Platform breakdown ────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Applications by Platform")
by_platform = stats.get("by_platform", {})
if by_platform:
    import pandas as pd

    platform_df = pd.DataFrame(
        list(by_platform.items()), columns=["Platform", "Count"]
    ).sort_values("Count", ascending=False)
    st.bar_chart(platform_df.set_index("Platform"))
else:
    st.info("No data yet.")
