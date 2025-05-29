import streamlit as st
import sys
import os

# Add parent directory to path to import from main app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import all your existing functions from app.py
from app import (
    get_db_connection_pool, get_connection, return_connection, 
    DATABASE_URL, RESET_PASSWORD, OFFICE_TIMEZONE, oasis
)
from allocate_rooms import run_allocation
import pandas as pd
from datetime import datetime, timedelta
import pytz

st.set_page_config(page_title="Oasis Overview", layout="wide")

# --- Copy your existing oasis functions here ---
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

            all_days = ["Monday", "Tuesday", "Wednesday", "Thursday"]
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
            cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, submission_time FROM oasis_preferences")
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=["Person", "Day 1", "Day 2", "Submitted At"])
    except Exception as e:
        st.warning(f"Failed to fetch oasis preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

# --- Main Page Content ---
def main():
    pool = get_db_connection_pool()
    
    st.title("🌿 Oasis Overview and Manual Signup")
    
    # --- Current Oasis Allocations ---
    st.header("📊 Current Oasis Allocations")
    oasis_df = get_oasis_grid(pool)
    if not oasis_df.empty:
        st.dataframe(oasis_df, use_container_width=True)
    else:
        st.info("No Oasis allocations yet.")
    
    # --- Submitted Oasis Preferences ---
    st.header("📝 Submitted Oasis Preferences")
    prefs_df = get_oasis_preferences(pool)
    if not prefs_df.empty:
        st.dataframe(prefs_df, use_container_width=True)
        st.info(f"📊 **Summary:** {len(prefs_df)} people submitted preferences")
    else:
        st.info("No Oasis preferences submitted yet.")
    
    # --- Copy your existing manual add form here ---
    today = datetime.now(OFFICE_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())

    st.header("Add yourself to Oasis Allocation - Personally - Anytime, if there is availability")
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

    # --- Copy your entire existing weekly overview section here ---
    st.header("📊 Full Weekly Oasis Overview")

    today = datetime.now(OFFICE_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    days = [this_monday + timedelta(days=i) for i in range(5)]  # Monday to Friday
    day_names = [d.strftime("%A") for d in days]
    capacity = oasis["capacity"]

    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, date FROM weekly_allocations WHERE room_name = 'Oasis'")
            rows = cur.fetchall()

        df = pd.DataFrame(rows, columns=["Name", "Date"])
        df["Date"] = pd.to_datetime(df["Date"]).dt.date

        unique_names = sorted(set(df["Name"]).union({"Niek"}))  # Always include Niek
        matrix = pd.DataFrame(False, index=unique_names, columns=day_names)

        for day, label in zip(days, day_names):
            signed_up = df[df["Date"] == day]["Name"]
            for name in signed_up:
                matrix.at[name, label] = True
        for day in day_names:
            matrix.at["Niek", day] = True  # Force Niek to always be signed up

        # --- Display availability ---
        st.subheader("🪑 Oasis Availability Summary")
        used_per_day = df.groupby("Date").size().to_dict()
        for day, label in zip(days, day_names):
            used = used_per_day.get(day, 0)
            if matrix.at["Niek", label]:
                used += 0 if "Niek" not in df[df["Date"] == day]["Name"].values else 0
            left = max(0, capacity - used)
            st.markdown(f"**{label}**: {left} spots left")

        # --- Display editable matrix ---
        edited = st.data_editor(
            matrix,
            use_container_width=True,
            disabled=["Niek"],
            key="oasis_matrix_editor"
        )

        if st.button("💾 Save Oasis Matrix"):
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND team_name != 'Niek'")
                    inserted_counts = {day: 1 if matrix.at["Niek", day] else 0 for day in day_names}

                    for name in edited.index:
                        if name == "Niek":
                            continue
                        for day in day_names:
                            if edited.at[name, day]:
                                if inserted_counts[day] < capacity:
                                    date_obj = this_monday + timedelta(days=day_names.index(day))
                                    cur.execute(
                                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                        (name, "Oasis", date_obj)
                                    )
                                    inserted_counts[day] += 1
                                else:
                                    st.warning(f"{name} could not be added to {day}: full.")
                    conn.commit()
                    st.success("✅ Matrix saved.")
                    st.rerun()

            except Exception as e:
                st.error(f"❌ Failed to save matrix: {e}")

    except Exception as e:
        st.error(f"❌ Error loading matrix: {e}")
    finally:
        return_connection(pool, conn)
    
    # --- Copy your existing oasis admin section here ---
    with st.expander("🔐 Admin Controls"):
        pwd = st.text_input("Enter admin password:", type="password", key="oasis_admin")
        if pwd == RESET_PASSWORD:
            st.success("✅ Access granted.")

            st.subheader("🌿 Oasis Admin")
            if st.button("🎲 Run Oasis Allocation"):
                success, _ = run_allocation(DATABASE_URL, only="oasis")
                if success:
                    st.success("✅ Oasis allocation completed.")
                    st.rerun()
                else:
                    st.error("❌ Oasis allocation failed.")

            # --- Reset Oasis Data ---
            st.subheader("🌾 Reset Oasis Data")
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

            if st.button("🧽 Remove Oasis Preferences"):
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

            # --- Oasis Preferences Editing (SAFE, supports 5 days) ---
            st.subheader("🌿 Oasis Preferences")
            df2 = get_oasis_preferences(pool)
            if not df2.empty:
                editable_oasis_df = st.data_editor(df2, num_rows="dynamic", use_container_width=True, key="edit_oasis")
                if st.button("💾 Save Oasis Changes"):
                    try:
                        conn = get_connection(pool)
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM oasis_preferences")
                            for _, row in editable_oasis_df.iterrows():
                                cur.execute("""
                                    INSERT INTO oasis_preferences (
                                        person_name,
                                        preferred_day_1,
                                        preferred_day_2,
                                        preferred_day_3,
                                        preferred_day_4,
                                        preferred_day_5,
                                        submission_time
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                                """, (
                                    row["Person"],
                                    row["Day 1"],
                                    row["Day 2"],
                                    row.get("Day 3"),
                                    row.get("Day 4"),
                                    row.get("Day 5"),
                                    row["Submitted At"]
                                ))
                            conn.commit()
                        st.success("✅ Oasis preferences updated.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Failed to update oasis preferences: {e}")
                    finally:
                        return_connection(pool, conn)
            else:
                st.info("No oasis preferences submitted yet.")
        elif pwd:
            st.error("❌ Incorrect password.")

if __name__ == "__main__":
    main()