# Fixes for button jumping issue in app.py
# Replace the problematic button sections with these improved versions

# 1. Add session state initialization at the top after imports
def initialize_button_states():
    """Initialize session state for button management"""
    button_states = [
        "confirm_proj_prefs_delete",
        "confirm_oasis_prefs_delete", 
        "confirm_proj_alloc_delete",
        "confirm_oasis_alloc_delete",
        "show_proj_prefs_confirm",
        "show_oasis_prefs_confirm"
    ]
    
    for state in button_states:
        if state not in st.session_state:
            st.session_state[state] = False

# Call this after your existing session state initializations
initialize_button_states()

# 2. Replace the problematic "Remove All Project Room Preferences" section
# Find this in your app.py around line 482 and replace with:

st.subheader("🧹 Reset Project Room Data")

# Remove allocations (simple button - less dangerous)
if st.button(f"🗑️ Remove Project Allocations for Current Week", key="btn_reset_proj_alloc_week"):
    conn_reset_pra = get_connection(pool)
    if conn_reset_pra:
        try:
            with conn_reset_pra.cursor() as cur:
                mon_to_reset = st.session_state.project_rooms_display_monday
                cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis' AND date >= %s AND date <= %s", (mon_to_reset, mon_to_reset + timedelta(days=6))) 
                conn_reset_pra.commit()
                st.success(f"✅ Project room allocations removed.")
                st.rerun()
        except Exception as e: 
            st.error(f"❌ Failed to reset project allocations: {e}")
            conn_reset_pra.rollback()
        finally: 
            return_connection(pool, conn_reset_pra)

# Remove preferences (dangerous - needs confirmation)
if not st.session_state.show_proj_prefs_confirm:
    if st.button("🧽 Remove All Project Room Preferences (Global Action)", key="btn_reset_all_proj_prefs"):
        st.session_state.show_proj_prefs_confirm = True
        st.rerun()
else:
    st.warning("⚠️ This will permanently delete ALL project room preferences!")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Yes, Delete All Preferences", key="btn_confirm_delete_proj_prefs"):
            conn_reset_prp = get_connection(pool)
            if conn_reset_prp:
                try:
                    with conn_reset_prp.cursor() as cur:
                        # First backup to archive (if you've added the archive tables)
                        try:
                            cur.execute("""
                                INSERT INTO weekly_preferences_archive 
                                (team_name, contact_person, team_size, preferred_days, submission_time, deleted_by, deletion_reason)
                                SELECT team_name, contact_person, team_size, preferred_days, submission_time, 'admin', 'Manual deletion via admin panel'
                                FROM weekly_preferences
                            """)
                        except:
                            pass  # Archive table might not exist yet
                        
                        # Delete the preferences
                        cur.execute("DELETE FROM weekly_preferences")
                        conn_reset_prp.commit()
                        st.success("✅ All project room preferences removed.")
                        st.session_state.show_proj_prefs_confirm = False
                        st.rerun()
                except Exception as e: 
                    st.error(f"❌ Failed: {e}")
                    conn_reset_prp.rollback()
                finally: 
                    return_connection(pool, conn_reset_prp)
    
    with col2:
        if st.button("❌ Cancel", key="btn_cancel_delete_proj_prefs"):
            st.session_state.show_proj_prefs_confirm = False
            st.rerun()

# 3. Similar fix for Oasis preferences section
# Find the Oasis section and replace with:

st.subheader("🌾 Reset Oasis Data")

# Remove allocations (simple button)
if st.button(f"🗑️ Remove Oasis Allocations for Current Week", key="btn_reset_oasis_alloc_week"):
    conn_reset_oa = get_connection(pool)
    if conn_reset_oa:
        try:
            with conn_reset_oa.cursor() as cur:
                mon_to_reset = st.session_state.oasis_display_monday
                cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND date >= %s AND date <= %s", (mon_to_reset, mon_to_reset + timedelta(days=6))) 
                conn_reset_oa.commit()
                st.success(f"✅ Oasis allocations removed.")
                st.rerun()
        except Exception as e: 
            st.error(f"❌ Failed to reset Oasis allocations: {e}")
            conn_reset_oa.rollback()
        finally: 
            return_connection(pool, conn_reset_oa)

# Remove preferences (dangerous - needs confirmation)
if not st.session_state.show_oasis_prefs_confirm:
    if st.button("🧽 Remove All Oasis Preferences (Global Action)", key="btn_reset_all_oasis_prefs"):
        st.session_state.show_oasis_prefs_confirm = True
        st.rerun()
else:
    st.warning("⚠️ This will permanently delete ALL Oasis preferences!")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Yes, Delete All Preferences", key="btn_confirm_delete_oasis_prefs"):
            conn_reset_op = get_connection(pool)
            if conn_reset_op:
                try:
                    with conn_reset_op.cursor() as cur:
                        # First backup to archive (if available)
                        try:
                            cur.execute("""
                                INSERT INTO oasis_preferences_archive 
                                (person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5, 
                                 submission_time, deleted_by, deletion_reason)
                                SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5,
                                       submission_time, 'admin', 'Manual deletion via admin panel'
                                FROM oasis_preferences
                            """)
                        except:
                            pass  # Archive table might not exist yet
                        
                        cur.execute("DELETE FROM oasis_preferences")
                        conn_reset_op.commit()
                        st.success("✅ All Oasis preferences removed.")
                        st.session_state.show_oasis_prefs_confirm = False
                        st.rerun()
                except Exception as e: 
                    st.error(f"❌ Failed: {e}")
                    conn_reset_op.rollback()
                finally: 
                    return_connection(pool, conn_reset_op)
    
    with col2:
        if st.button("❌ Cancel", key="btn_cancel_delete_oasis_prefs"):
            st.session_state.show_oasis_prefs_confirm = False
            st.rerun()
