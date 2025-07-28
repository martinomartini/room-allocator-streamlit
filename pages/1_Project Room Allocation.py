import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
from allocate_rooms import run_allocation, get_db_connection_pool, get_room_grid, get_preferences

st.set_page_config(page_title="Project Room Allocation", layout="wide")

OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")
pool = get_db_connection_pool()

st.title("📌 Project Room Allocation")

# --- Allocation Table ---
st.subheader("🔍 Current Weekly Room Grid")
alloc_df = get_room_grid(pool)
if alloc_df.empty:
    st.info("No allocations yet.")
else:
    st.dataframe(alloc_df, use_container_width=True)

# --- Admin Section ---
st.subheader("🔐 Admin Controls")
pwd = st.text_input("Enter admin password:", type="password")
if pwd == "boom123":
    st.success("✅ Access granted.")

    if st.button("🚀 Run Project Room Allocation"):
        success, _ = run_allocation(st.secrets["SUPABASE_DB_URI"], only="project")
        if success:
            st.success("✅ Project room allocation completed.")
        else:
            st.error("❌ Project room allocation failed.")

    df1 = get_preferences(pool)
    st.subheader("🧾 Team Preferences")
    if not df1.empty:
        editable_team_df = st.data_editor(df1, num_rows="dynamic", use_container_width=True, key="edit_teams")
        if st.button("💾 Save Team Preferences"):
            try:
                conn = pool.getconn()
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM weekly_preferences")
                    for _, row in editable_team_df.iterrows():
                        cur.execute("""
                            INSERT INTO weekly_preferences (team_name, contact_person, team_size, preferred_days, submission_time)
                            VALUES (%s, %s, %s, %s, NOW())
                        """, (row["Team"], row["Contact"], int(row["Size"]), row["Days"]))
                    conn.commit()
                st.success("✅ Team preferences updated.")
            except Exception as e:
                st.error(f"❌ Failed to update: {e}")
            finally:
                pool.putconn(conn)
    else:
        st.info("No team preferences submitted yet.")
else:
    if pwd:
        st.error("❌ Incorrect password.")