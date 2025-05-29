import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
import sys
import os

# Import from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(page_title="Oasis Overview", layout="wide")

# --- Configuration ---
DATABASE_URL = st.secrets.get("SUPABASE_DB_URI", os.environ.get("SUPABASE_DB_URI"))
OFFICE_TIMEZONE_STR = st.secrets.get("OFFICE_TIMEZONE", os.environ.get("OFFICE_TIMEZONE", "UTC"))
RESET_PASSWORD = "trainee"

try:
    OFFICE_TIMEZONE = pytz.timezone(OFFICE_TIMEZONE_STR)
except pytz.UnknownTimeZoneError:
    OFFICE_TIMEZONE = pytz.utc

# --- Import from main app ---
try:
    from app import get_db_connection_pool, get_connection, return_connection, oasis
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

from allocate_rooms import run_allocation

# --- Functions ---
def get_oasis_grid(pool):
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, room_name, date FROM weekly_allocations WHERE room_name = 'Oasis'")
            data = cur.fetchall()
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data, columns=["Person", "Room", "Date"])
            df["Date"] = pd.to_datetime(df["Date"])
            df["Day"] = df["Date"].dt.strftime('%A')

            all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            grouped = df.groupby("Day")["Person"].apply(lambda x: ", ".join(sorted(set(x))))
            grouped = grouped.reindex(all_days, fill_value="Vacant").reset_index()
            grouped = grouped.rename(columns={"Day": "Weekday", "Person": "People"})

            return grouped
    except Exception as e:
        st.warning(f"Failed to load oasis allocation data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_oasis_preferences(pool):
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, submission_time FROM oasis_preferences")
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
            
            # Process the data to show all preferred days in a readable format
            processed_rows = []
            for row in rows:
                name = row[0]
                days = [day for day in row[1:6] if day is not None]
                days_str = ", ".join(days) if days else "None"
                processed_rows.append([name, days_str, row[6]])
            
            return pd.DataFrame(processed_rows, columns=["Person", "Preferred Days", "Submitted At"])
    except Exception as e:
        st.warning(f"Failed to fetch oasis preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

# --- Main Content ---
pool = get_db_connection_pool()

st.title("🌿 Oasis Overview and Manual Signup")

# Display current time
now_local = datetime.now(OFFICE_TIMEZONE)
st.info(f"**Current Office Time:** {now_local.strftime('%Y-%m-%d %H:%M:%S')} ({OFFICE_TIMEZONE_STR})")

# Current allocations
st.header("📊 Current Oasis Allocations")
oasis_df = get_oasis_grid(pool)
if not oasis_df.empty:
    st.dataframe(oasis_df, use_container_width=True)
else:
    st.info("No Oasis allocations yet.")

# Preferences
st.header("📝 Submitted Oasis Preferences")
prefs_df = get_oasis_preferences(pool)
if not prefs_df.empty:
    st.dataframe(prefs_df, use_container_width=True)
    st.info(f"📊 **Summary:** {len(prefs_df)} people submitted preferences")
else:
    st.info("No Oasis preferences submitted yet.")

# Manual add form
today = datetime.now(OFFICE_TIMEZONE).date()
this_monday = today - timedelta(days=today.weekday())

st.header("➕ Add Yourself to Oasis (Emergency/Manual)")
st.warning("⚠️ Use this only if you missed the regular submission deadline!")

with st.form("oasis_add_form"):
    new_name = st.text_input("Your Name")
    new_days = st.multiselect("Select one or more days:", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    add_submit = st.form_submit_button("➕ Add me to the schedule")

    if add_submit:
        if not new_name.strip():
            st.error("❌ Please enter your name.")
        elif len(new_days) == 0:
            st.error("❌ Select at least one day.")
        else:
            conn = None
            try:
                conn = get_connection(pool)
                with conn.cursor() as cur:
                    name_clean = new_name.strip().title()

                    # Remove existing entries for this user
                    cur.execute("""
                        DELETE FROM weekly_allocations
                        WHERE room_name = 'Oasis' AND team_name = %s
                    """, (name_clean,))

                    for day in new_days:
                        date_obj = this_monday + timedelta(days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].index(day))

                        # Check current occupancy
                        cur.execute("""
                            SELECT COUNT(*) FROM weekly_allocations
                            WHERE room_name = 'Oasis' AND date = %s
                        """, (date_obj,))
                        count = cur.fetchone()[0]

                        if count >= oasis["capacity"]:
                            st.warning(f"Oasis is full on {day}, not added.")
                        else:
                            cur.execute("""
                                INSERT INTO weekly_allocations (team_name, room_name, date)
                                VALUES (%s, 'Oasis', %s)
                            """, (name_clean, date_obj))

                    conn.commit()
                    st.success("✅ You're added to the selected days!")
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Error: {e}")
            finally:
                if conn:
                    return_connection(pool, conn)

# Admin controls
with st.expander("🔐 Admin Controls"):
    pwd = st.text_input("Enter admin password:", type="password", key="oasis_admin")
    if pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")

        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🎲 Run Oasis Allocation"):
                with st.spinner("Running oasis allocation..."):
                    success, _ = run_allocation(DATABASE_URL, only="oasis")
                    if success:
                        st.success("✅ Oasis allocation completed.")
                        st.rerun()
                    else:
                        st.error("❌ Oasis allocation failed.")

        with col2:
            if st.button("🗑️ Remove Oasis Allocations"):
                conn = get_connection(pool)
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'")
                        conn.commit()
                        st.success("✅ Oasis allocations removed.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to remove oasis allocations: {e}")
                finally:
                    return_connection(pool, conn)

        st.subheader("🗑️ Reset Data")
        if st.button("🧽 Remove Oasis Preferences", type="secondary"):
            conn = get_connection(pool)
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM oasis_preferences")
                    conn.commit()
                    st.success("✅ Oasis preferences removed.")
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to remove oasis preferences: {e}")
            finally:
                return_connection(pool, conn)
    elif pwd:
        st.error("❌ Incorrect password.")