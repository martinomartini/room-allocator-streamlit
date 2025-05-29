import streamlit as st
import sys
import os

# Add parent directory to path to import from main app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import all your existing functions from app.py
from app import (
    get_db_connection_pool, get_connection, return_connection, 
    DATABASE_URL, RESET_PASSWORD, OFFICE_TIMEZONE, AVAILABLE_ROOMS
)
from allocate_rooms import run_allocation
import pandas as pd
from datetime import datetime, timedelta
import pytz
from psycopg2.extras import RealDictCursor

st.set_page_config(page_title="Project Room Allocation", layout="wide")

# --- Copy your existing database functions here ---
def get_room_grid(pool):
    # Set up current week's Monday and day mapping
    OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")
    today = datetime.now(OFFICE_TIMEZONE).date()
    this_monday = today - timedelta(days=today.weekday())
    day_mapping = {
        this_monday + timedelta(days=0): "Monday",
        this_monday + timedelta(days=1): "Tuesday",
        this_monday + timedelta(days=2): "Wednesday",
        this_monday + timedelta(days=3): "Thursday"
    }

    day_labels = list(day_mapping.values())

    # Load all room names from rooms.json (excluding Oasis)
    all_rooms = [r["name"] for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]

    # Start with every room marked Vacant
    grid = {
        room: {**{"Room": room}, **{day: "Vacant" for day in day_labels}}
        for room in all_rooms
    }

    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Fetch project room allocations
            cur.execute("""
                SELECT team_name, room_name, date
                FROM weekly_allocations
                WHERE room_name != 'Oasis'
            """)
            allocations = cur.fetchall()

            # Fetch team contact info
            cur.execute("""
                SELECT team_name, contact_person
                FROM weekly_preferences
            """)
            contacts = {row["team_name"]: row["contact_person"] for row in cur.fetchall()}

        # Fill in the grid with actual allocations
        for row in allocations:
            team = row["team_name"]
            room = row["room_name"]
            date = row["date"]
            day = day_mapping.get(date)

            if room not in grid or not day:
                continue

            contact = contacts.get(team)
            label = f"{team} ({contact})" if contact else team
            grid[room][day] = label

        return pd.DataFrame(grid.values())

    finally:
        pool.putconn(conn)

def get_unallocated_teams(pool):
    """Get teams that submitted preferences but didn't get allocated"""
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            # Get all teams that submitted preferences
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days FROM weekly_preferences")
            all_teams = cur.fetchall()
            
            # Get teams that got allocated
            cur.execute("SELECT DISTINCT team_name FROM weekly_allocations WHERE room_name != 'Oasis'")
            allocated_teams = {row[0] for row in cur.fetchall()}
            
            # Find unallocated teams
            unallocated = [team for team in all_teams if team[0] not in allocated_teams]
            
            return pd.DataFrame(unallocated, columns=["Team", "Contact", "Size", "Preferred Days"])
    except Exception as e:
        st.warning(f"Failed to fetch unallocated teams: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_preferences(pool):
    conn = get_connection(pool)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_name, contact_person, team_size, preferred_days FROM weekly_preferences")
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=["Team", "Contact", "Size", "Days"])
    except Exception as e:
        st.warning(f"Failed to fetch preferences: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

# --- Main Page Content ---
def main():
    pool = get_db_connection_pool()
    
    st.title("📊 Project Room Allocation")
    
    # --- Current Project Room Allocations ---
    st.header("📌 Current Project Room Allocations")
    alloc_df = get_room_grid(pool)
    if alloc_df.empty:
        st.info("No project room allocations generated yet.")
        st.info("💡 Use the admin controls below to run the allocation algorithm.")
    else:
        st.dataframe(alloc_df, use_container_width=True)
    
    # --- Teams Without Rooms ---
    st.header("⚠️ Teams That Submitted Preferences But Weren't Allocated")
    unallocated_df = get_unallocated_teams(pool)
    if unallocated_df.empty:
        if not alloc_df.empty:
            st.success("✅ All teams that submitted preferences have been allocated!")
        else:
            st.info("ℹ️ No preferences submitted yet.")
    else:
        st.warning(f"⚠️ {len(unallocated_df)} teams submitted preferences but weren't allocated:")
        st.dataframe(unallocated_df, use_container_width=True)
        
        with st.expander("💡 Why might teams not be allocated?"):
            st.markdown("""
            **Possible reasons for non-allocation:**
            - Team size exceeds available room capacity (max 6 people)
            - No rooms available for their preferred days (Monday/Wednesday or Tuesday/Thursday)
            - Submitted after the deadline
            - All appropriate rooms already allocated to other teams
            - Technical issues with the submission
            
            **What to do:**
            - Contact admin for manual allocation if space becomes available
            - Consider splitting large teams
            - Check if submission was successful
            """)
    
    # --- All Submitted Preferences ---
    st.header("📝 All Submitted Project Team Preferences")
    prefs_df = get_preferences(pool)
    if not prefs_df.empty:
        st.dataframe(prefs_df, use_container_width=True)
        st.info(f"📊 **Summary:** {len(prefs_df)} teams submitted preferences")
    else:
        st.info("No team preferences submitted yet.")
    
    # --- Copy your entire existing admin section here ---
    with st.expander("🔐 Admin Controls"):
        pwd = st.text_input("Enter admin password:", type="password")
        if pwd == RESET_PASSWORD:
            st.success("✅ Access granted.")

            # --- Allocation Controls ---
            st.subheader("🧠 Project Room Admin")
            if st.button("🚀 Run Project Room Allocation"):
                success, _ = run_allocation(DATABASE_URL, only="project")
                if success:
                    st.success("✅ Project room allocation completed.")
                    st.rerun()
                else:
                    st.error("❌ Project room allocation failed.")

            # --- Project Room Allocations Editing ---
            st.subheader("📌 Project Room Allocations")
            try:
                alloc_df = get_room_grid(pool)
                if not alloc_df.empty:
                    editable_alloc = st.data_editor(alloc_df, num_rows="dynamic", use_container_width=True, key="edit_allocations")
                    if st.button("💾 Save Project Room Allocation Changes"):
                        try:
                            today = datetime.now(OFFICE_TIMEZONE).date()
                            this_monday = today - timedelta(days=today.weekday())
                            conn = get_connection(pool)
                            with conn.cursor() as cur:
                                cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
                                for _, row in editable_alloc.iterrows():
                                    for day in ["Monday", "Tuesday", "Wednesday", "Thursday"]:
                                        value = row.get(day, "")
                                        if value and value != "Vacant":
                                            team_info = str(value)
                                            team = team_info.split("(")[0].strip()
                                            room = str(row["Room"]) if pd.notnull(row["Room"]) else None
                                            date_obj = this_monday + timedelta(days=["Monday", "Tuesday", "Wednesday", "Thursday"].index(day))
                                            if team and room:
                                                cur.execute("""
                                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                                    VALUES (%s, %s, %s)
                                                """, (team, room, date_obj))
                                conn.commit()
                            st.success("✅ Manual allocations updated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Failed to save project room allocations: {e}")
                        finally:
                            return_connection(pool, conn)
                else:
                    st.info("No allocations yet to edit.")
            except Exception as e:
                st.warning(f"Failed to load allocation data: {e}")

            # --- Reset Project Room Data ---
            st.subheader("🧹 Reset Project Room Data")
            if st.button("🗑️ Remove Project Room Allocations"):
                conn = get_connection(pool)
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
                        conn.commit()
                        st.success("✅ Project room allocations removed.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to remove project room allocations: {e}")
                finally:
                    return_connection(pool, conn)

            if st.button("🧽 Remove Project Room Preferences"):
                conn = get_connection(pool)
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM weekly_preferences")
                        conn.commit()
                        st.success("✅ Project room preferences removed.")
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to remove project preferences: {e}")
                finally:
                    return_connection(pool, conn)

            # --- Team Preferences Editing ---
            st.subheader("🧾 Team Preferences")
            df1 = get_preferences(pool)
            if not df1.empty:
                editable_team_df = st.data_editor(df1, num_rows="dynamic", use_container_width=True, key="edit_teams")
                if st.button("💾 Save Team Changes"):
                    try:
                        conn = get_connection(pool)
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
                    finally:
                        return_connection(pool, conn)
            else:
                st.info("No team preferences submitted yet.")
        elif pwd:
            st.error("❌ Incorrect password.")

if __name__ == "__main__":
    main()