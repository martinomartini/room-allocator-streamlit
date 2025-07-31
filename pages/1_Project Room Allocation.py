import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz

# Import refactored modules
from config import config
from utils.database import get_db_manager, get_room_manager, get_preference_manager
from utils.validation import validate_admin_password

st.set_page_config(page_title="Project Room Allocation", layout="wide")

# Initialize managers
db_manager = get_db_manager()
room_manager = get_room_manager()
preference_manager = get_preference_manager()

st.title("📌 Project Room Allocation")

# --- Allocation Table ---
st.subheader("🔍 Current Weekly Room Grid")
alloc_df = room_manager.get_room_grid(config.STATIC_PROJECT_MONDAY)
if alloc_df.empty:
    st.info("No allocations yet.")
else:
    st.dataframe(alloc_df, use_container_width=True)

# --- Admin Section ---
st.subheader("🔐 Admin Controls")
pwd = st.text_input("Enter admin password:", type="password")
if validate_admin_password(pwd):
    st.success("✅ Access granted.")

    if st.button("🚀 Run Project Room Allocation"):
        try:
            from allocate_rooms import run_allocation
            success, _ = run_allocation(config.DATABASE_URL, only="project", base_monday_date=config.STATIC_PROJECT_MONDAY)
            if success:
                st.success("✅ Project room allocation completed.")
                st.rerun()
            else:
                st.error("❌ Project room allocation failed.")
        except ImportError:
            st.error("❌ Allocation function not available.")

    df1 = room_manager.get_preferences()
    st.subheader("🧾 Team Preferences")
    if not df1.empty:
        editable_team_df = st.data_editor(df1, num_rows="dynamic", use_container_width=True, key="edit_teams")
        if st.button("💾 Save Team Preferences"):
            try:
                conn = db_manager.get_connection()
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM weekly_preferences")
                    for _, row in editable_team_df.iterrows():
                        cur.execute("""
                            INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
                            VALUES (%s, %s, %s, %s, NOW())
                        """, (row["Team"], row["Contact"], int(row["Size"]), row["Days"]))
                    conn.commit()
                st.success("✅ Team preferences updated.")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to update team preferences: {e}")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    db_manager.return_connection(conn)
    else:
        st.info("No team preferences submitted yet.")
elif pwd:
    st.error("❌ Incorrect password.")
            except Exception as e:
                st.error(f"❌ Failed to update: {e}")
            finally:
                pool.putconn(conn)
    else:
        st.info("No team preferences submitted yet.")
else:
    if pwd:
        st.error("❌ Incorrect password.")