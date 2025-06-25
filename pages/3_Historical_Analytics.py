import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, date
import pytz
import sys
import os

# Import from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(page_title="Historical Data & Analytics", layout="wide")

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
    from app import get_db_connection_pool, get_connection, return_connection, AVAILABLE_ROOMS, STATIC_OASIS_MONDAY
    # Calculate room counts from rooms.json
    PROJECT_ROOMS = [r for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]
    OASIS_ROOM = next((r for r in AVAILABLE_ROOMS if r["name"] == "Oasis"), {"capacity": 16})
    
    TOTAL_PROJECT_ROOMS = len(PROJECT_ROOMS)
    OASIS_CAPACITY = OASIS_ROOM["capacity"]
    
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

# --- UI Week Logic (matches main app exactly) ---
def get_ui_week_dates():
    """Get the exact same 5-day period that the UI displays"""
    # Initialize session state if needed (matches main app logic)
    if "oasis_display_monday" not in st.session_state:
        st.session_state.oasis_display_monday = STATIC_OASIS_MONDAY
    
    # Calculate the 5 days the UI displays (matches main app exactly)
    oasis_overview_monday_display = st.session_state.oasis_display_monday 
    oasis_overview_days_dates = [oasis_overview_monday_display + timedelta(days=i) for i in range(5)]
    
    return oasis_overview_days_dates

# --- Historical Data Functions ---
def get_historical_allocations(pool, start_date=None, end_date=None):
    """Get historical allocation data with room type classification"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            # Check if confirmed column exists
            cur.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'weekly_allocations_archive' AND column_name = 'confirmed'
            """)
            has_confirmed_col = cur.fetchone() is not None
            
            if start_date and end_date:
                if has_confirmed_col:
                    cur.execute("""
                        SELECT team_name, room_name, date, 
                               EXTRACT(DOW FROM date) as day_of_week,
                               EXTRACT(WEEK FROM date) as week_number,
                               EXTRACT(YEAR FROM date) as year,
                               COALESCE(confirmed, FALSE) as confirmed
                        FROM weekly_allocations_archive 
                        WHERE date >= %s AND date <= %s
                        ORDER BY date DESC
                    """, (start_date, end_date))
                else:
                    cur.execute("""
                        SELECT team_name, room_name, date, 
                               EXTRACT(DOW FROM date) as day_of_week,
                               EXTRACT(WEEK FROM date) as week_number,
                               EXTRACT(YEAR FROM date) as year,
                               TRUE as confirmed
                        FROM weekly_allocations_archive 
                        WHERE date >= %s AND date <= %s
                        ORDER BY date DESC
                    """, (start_date, end_date))
            else:
                if has_confirmed_col:
                    cur.execute("""
                        SELECT team_name, room_name, date,
                               EXTRACT(DOW FROM date) as day_of_week,
                               EXTRACT(WEEK FROM date) as week_number,
                               EXTRACT(YEAR FROM date) as year,
                               COALESCE(confirmed, FALSE) as confirmed
                        FROM weekly_allocations_archive 
                        ORDER BY date DESC
                        LIMIT 1000
                    """)
                else:
                    cur.execute("""
                        SELECT team_name, room_name, date,
                               EXTRACT(DOW FROM date) as day_of_week,
                               EXTRACT(WEEK FROM date) as week_number,
                               EXTRACT(YEAR FROM date) as year,
                               TRUE as confirmed
                        FROM weekly_allocations_archive 
                        ORDER BY date DESC
                        LIMIT 1000
                    """)
            
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
                
            df = pd.DataFrame(rows, columns=["Team", "Room", "Date", "DayOfWeek", "WeekNumber", "Year", "Confirmed"])
            df['Date'] = pd.to_datetime(df['Date'])
            df['WeekDay'] = df['Date'].dt.day_name()
            df['WeekStart'] = df['Date'] - pd.to_timedelta(df['Date'].dt.dayofweek, unit='d')
            
            # Add room type classification
            df['Room_Type'] = df['Room'].apply(lambda x: 'Oasis' if x == 'Oasis' else 'Project Room')
            
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch historical data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_preferences_data(pool, weeks_back=12):
    """Get preferences data for both project teams and Oasis users"""
    if not pool: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    conn = get_connection(pool)
    if not conn: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    
    try:
        with conn.cursor() as cur:
            # Get project preferences
            cur.execute("""
                SELECT team_name, contact_person, team_size, preferred_days, submission_time
                FROM weekly_preferences
                ORDER BY submission_time DESC
            """)
            project_rows = cur.fetchall()
            
            # Get Oasis preferences  
            cur.execute("""
                SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, 
                       preferred_day_4, preferred_day_5, submission_time
                FROM oasis_preferences
                ORDER BY submission_time DESC
            """)
            oasis_rows = cur.fetchall()
            
            project_df = pd.DataFrame(project_rows, columns=[
                "Team", "Contact", "Size", "Preferred_Days", "Submission_Time"
            ]) if project_rows else pd.DataFrame()
            
            oasis_df = pd.DataFrame(oasis_rows, columns=[
                "Person", "Day1", "Day2", "Day3", "Day4", "Day5", "Submission_Time"
            ]) if oasis_rows else pd.DataFrame()
            
            return {'project': project_df, 'oasis': oasis_df}
            
    except Exception as e:
        st.error(f"Failed to fetch preferences data: {e}")
        return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    finally:
        return_connection(pool, conn)

def get_usage_statistics(pool, weeks_back=12):
    """Get usage statistics for analysis with room type separation"""
    if not pool: return {}
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    preferences = get_preferences_data(pool, weeks_back)
    
    if df.empty:
        return {}    # Separate project rooms and Oasis
    project_df = df[df['Room_Type'] == 'Project Room']
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    # For Oasis, match UI behavior: count ALL allocations, not just confirmed
    # UI shows "spots left" based on all allocations in weekly_allocations table
    # oasis_confirmed_df = oasis_df[oasis_df['Confirmed'] == True]  # REMOVED to match UI
    oasis_for_stats = oasis_df  # Use all Oasis allocations to match UI logic
    
    # Calculate statistics
    stats = {
        'total_allocations': len(project_df) + len(oasis_for_stats),  # Match UI logic
        'project_allocations': len(project_df),
        'oasis_allocations': len(oasis_for_stats),  # All Oasis allocations to match UI
        'unique_teams': project_df['Team'].nunique() if not project_df.empty else 0,
        'unique_oasis_users': oasis_for_stats['Team'].nunique() if not oasis_for_stats.empty else 0,  # All users to match UI
        'unique_project_rooms': project_df['Room'].nunique() if not project_df.empty else 0,
        'date_range': f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}",
        'most_popular_project_room': project_df['Room'].mode().iloc[0] if not project_df.empty and len(project_df['Room'].mode()) > 0 else "N/A",
        'most_popular_day_projects': project_df['WeekDay'].mode().iloc[0] if not project_df.empty and len(project_df['WeekDay'].mode()) > 0 else "N/A",
        'most_popular_day_oasis': oasis_for_stats['WeekDay'].mode().iloc[0] if not oasis_for_stats.empty and len(oasis_for_stats['WeekDay'].mode()) > 0 else "N/A",  # All users to match UI
        'most_active_team': project_df['Team'].mode().iloc[0] if not project_df.empty and len(project_df['Team'].mode()) > 0 else "N/A",
        'current_project_preferences': len(preferences['project']),
        'current_oasis_preferences': len(preferences['oasis'])
    }
    
    return stats

def get_room_utilization(pool, weeks_back=8):
    """Calculate room utilization rates separated by type"""
    if not pool: return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return {'project': pd.DataFrame(), 'oasis': pd.DataFrame()}    # Separate project rooms and Oasis
    project_df = df[df['Room_Type'] == 'Project Room']
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    # For Oasis, match UI behavior: count ALL allocations, not just confirmed
    # UI shows "spots left" based on all allocations in weekly_allocations table
    # oasis_confirmed_df = oasis_df[oasis_df['Confirmed'] == True]  # REMOVED to match UI
    oasis_for_calc = oasis_df  # Use all Oasis allocations to match UI logic
    
    # Calculate project room utilization
    project_usage = pd.DataFrame()
    if not project_df.empty:
        project_usage = project_df.groupby('Room').size().reset_index(name='Usage_Count')
        # Assuming 4 days per week for project rooms
        total_possible_slots = 4 * weeks_back
        project_usage['Utilization_Rate'] = (project_usage['Usage_Count'] / total_possible_slots * 100).round(1)
        project_usage = project_usage.sort_values('Utilization_Rate', ascending=False)    # Calculate Oasis utilization (daily basis) - count unique people per day (matches UI logic)
    oasis_usage = pd.DataFrame()
    if not oasis_for_calc.empty:
        # Count unique people per day for Oasis (matches the "used_spots" calculation in the UI)
        oasis_daily = oasis_for_calc.groupby(['Date'])['Team'].nunique().reset_index()
        oasis_daily.columns = ['Date', 'Attendees_Count']
        
        # Calculate daily utilization rates
        oasis_daily['Daily_Utilization'] = (oasis_daily['Attendees_Count'] / OASIS_CAPACITY * 100).round(1)
        
        # Calculate average utilization across all days
        avg_attendees = oasis_daily['Attendees_Count'].mean()
        avg_utilization = oasis_daily['Daily_Utilization'].mean()
        max_attendees = oasis_daily['Attendees_Count'].max()
        
        oasis_usage = pd.DataFrame({
            'Room': ['Oasis'],
            'Total_Bookings': [len(oasis_for_calc)],  # All bookings to match UI logic
            'Avg_Daily_Attendees': [round(avg_attendees, 1)],
            'Max_Daily_Attendees': [max_attendees],
            'Avg_Daily_Utilization': [round(avg_utilization, 1)],
            'Capacity': [OASIS_CAPACITY]
        })
    
    return {'project': project_usage, 'oasis': oasis_usage}

def get_weekly_trends(pool, weeks_back=12):
    """Get weekly allocation trends"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Group by week
    weekly_trends = df.groupby('WeekStart').agg({
        'Team': 'count',
        'Room': 'nunique'
    }).reset_index()
    
    weekly_trends.columns = ['Week', 'Total_Allocations', 'Rooms_Used']
    weekly_trends['Week'] = weekly_trends['Week'].dt.strftime('%Y-%m-%d')
    
    return weekly_trends

def get_daily_utilization(pool, weeks_back=8):
    """Calculate daily utilization rates for project rooms and Oasis separately"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Calculate daily utilization
    daily_stats = []
    
    for date_val in df['Date'].dt.date.unique():
        day_data = df[df['Date'].dt.date == date_val]
        
        # Project rooms utilization
        project_rooms_used = len(day_data[day_data['Room_Type'] == 'Project Room']['Room'].unique())
        project_utilization = (project_rooms_used / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0        # Oasis utilization (count all people to match UI logic, not just confirmed)
        oasis_data = day_data[day_data['Room_Type'] == 'Oasis']
        # Count unique people per day (matches UI "used_spots" calculation)
        oasis_people = oasis_data['Team'].nunique()
        oasis_utilization = (oasis_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
        
        daily_stats.append({
            'Date': date_val,
            'WeekDay': day_data.iloc[0]['WeekDay'],
            'WeekStart': day_data.iloc[0]['WeekStart'].date(),
            'Project_Rooms_Used': project_rooms_used,
            'Project_Rooms_Total': TOTAL_PROJECT_ROOMS,
            'Project_Utilization': round(project_utilization, 1),
            'Oasis_People': oasis_people,
            'Oasis_Capacity': OASIS_CAPACITY,
            'Oasis_Utilization': round(oasis_utilization, 1)
        })
    
    return pd.DataFrame(daily_stats).sort_values('Date', ascending=False)

def get_current_allocations(pool):
    """Get current week allocation data from active table"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            # Check if confirmed column exists
            cur.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'weekly_allocations' AND column_name = 'confirmed'
            """)
            has_confirmed_col = cur.fetchone() is not None
            
            # Get all data from weekly_allocations table (revert to original logic)
            if has_confirmed_col:
                cur.execute("""
                    SELECT team_name, room_name, date, 
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year,
                           COALESCE(confirmed, FALSE) as confirmed
                    FROM weekly_allocations 
                    ORDER BY date DESC
                """)
            else:
                cur.execute("""
                    SELECT team_name, room_name, date, 
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year,
                           TRUE as confirmed
                    FROM weekly_allocations 
                    ORDER BY date DESC
                """)
            
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
                
            df = pd.DataFrame(rows, columns=["Team", "Room", "Date", "DayOfWeek", "WeekNumber", "Year", "Confirmed"])
            df['Date'] = pd.to_datetime(df['Date'])
            df['WeekDay'] = df['Date'].dt.day_name()
            df['WeekStart'] = df['Date'] - pd.to_timedelta(df['Date'].dt.dayofweek, unit='d')
            
            # Add room type classification
            df['Room_Type'] = df['Room'].apply(lambda x: 'Oasis' if x == 'Oasis' else 'Project Room')
            df['Data_Source'] = 'Current'
            
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch current allocation data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_combined_allocations(pool, start_date=None, end_date=None, include_current=True):
    """Get combined current and historical allocation data"""
    historical_df = get_historical_allocations(pool, start_date, end_date)
    current_df = pd.DataFrame()
    
    if include_current:
        current_df = get_current_allocations(pool)
        if not current_df.empty:
            current_df['Data_Source'] = 'Current'
    
    if not historical_df.empty:
        historical_df['Data_Source'] = 'Historical'
    
    # Combine datasets
    if not historical_df.empty and not current_df.empty:
        combined_df = pd.concat([current_df, historical_df], ignore_index=True)
    elif not historical_df.empty:
        combined_df = historical_df
    elif not current_df.empty:
        combined_df = current_df
    else:
        combined_df = pd.DataFrame()
    
    return combined_df

def get_oasis_daily_breakdown(pool, weeks_back=8):
    """Get detailed daily Oasis utilization breakdown - only confirmed entries"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Filter for Oasis only
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    if oasis_df.empty:
        return pd.DataFrame()
    
    # Filter to only confirmed Oasis entries (people who actually checked the box)
    oasis_df = oasis_df[oasis_df['Confirmed'] == True]
    
    if oasis_df.empty:
        return pd.DataFrame()
    
    # Calculate daily breakdown (only confirmed attendees)
    daily_breakdown = oasis_df.groupby(['Date', 'WeekDay']).agg({
        'Team': 'count'  # Count actual confirmed attendees per day
    }).reset_index()
    
    daily_breakdown.columns = ['Date', 'WeekDay', 'Attendees']
    daily_breakdown['Utilization_Rate'] = (daily_breakdown['Attendees'] / OASIS_CAPACITY * 100).round(1)
    daily_breakdown['Capacity'] = OASIS_CAPACITY
    daily_breakdown['Date'] = daily_breakdown['Date'].dt.strftime('%Y-%m-%d')
    
    # Reorder by weekday
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    daily_breakdown['WeekDay'] = pd.Categorical(daily_breakdown['WeekDay'], categories=day_order, ordered=True)
    daily_breakdown = daily_breakdown.sort_values(['Date', 'WeekDay'], ascending=[False, True])
    
    return daily_breakdown

def get_current_oasis_utilization(current_df, ui_week_dates=None):
    """Calculate current week Oasis utilization per day - matches UI 'spots left' calculation exactly"""
    if current_df.empty:
        return pd.DataFrame(), 0.0
      # Filter for Oasis allocations only
    oasis_df = current_df[current_df['Room_Type'] == 'Oasis']
    
    if oasis_df.empty:
        return pd.DataFrame(), 0.0
    
    # DEBUG: Print what we're working with
    st.write(f"🔍 **DEBUG get_current_oasis_utilization**: Input has {len(current_df)} total records, {len(oasis_df)} Oasis records")
    
    if ui_week_dates and len(ui_week_dates) > 0:
        st.write(f"🔍 **DEBUG**: UI week dates provided: {[d.strftime('%Y-%m-%d') for d in ui_week_dates]}")
        # Filter to exact same dates as UI displays
        # Handle both date and datetime objects
        if hasattr(ui_week_dates[0], 'date'):
            # datetime objects
            ui_dates_as_dates = [d.date() for d in ui_week_dates]
        else:
            # already date objects
            ui_dates_as_dates = ui_week_dates
        
        oasis_df = oasis_df[oasis_df['Date'].dt.date.isin(ui_dates_as_dates)]
        st.write(f"🔍 **DEBUG**: After filtering to UI dates: {len(oasis_df)} Oasis records")
    else:
        # Fallback: take first 5 chronological days (old logic)
        st.write(f"🔍 **DEBUG**: No UI dates provided, using fallback logic")
        st.write(f"🔍 **DEBUG**: Date range in data: {oasis_df['Date'].min()} to {oasis_df['Date'].max()}")
        
        unique_dates = sorted(oasis_df['Date'].dt.date.unique())
        if len(unique_dates) > 5:
            first_5_dates = unique_dates[:5]
            st.write(f"🔍 **DEBUG**: Filtering to first 5 days: {first_5_dates}")
            oasis_df = oasis_df[oasis_df['Date'].dt.date.isin(first_5_dates)]
        else:
            st.write(f"🔍 **DEBUG**: Using all {len(unique_dates)} days (≤5): {unique_dates}")
    
    # EXACT UI logic replication: count unique Team (Name in UI) per Date
    daily_counts = oasis_df.groupby(['Date', 'WeekDay'])['Team'].nunique().reset_index()
    daily_counts.columns = ['Date', 'WeekDay', 'Used_Spots']
    
    st.write(f"🔍 **DEBUG**: Groupby result: {len(daily_counts)} days")
    if not daily_counts.empty:
        st.write("🔍 **DEBUG**: Daily counts from analytics function:")
        debug_display = daily_counts.copy()
        debug_display['Date_Str'] = debug_display['Date'].dt.strftime('%Y-%m-%d')
        st.dataframe(debug_display[['Date_Str', 'WeekDay', 'Used_Spots']], use_container_width=True, hide_index=True)
    
    # Calculate utilization: Used_Spots / OASIS_CAPACITY * 100 (matches UI logic)
    daily_counts['Utilization'] = (daily_counts['Used_Spots'] / OASIS_CAPACITY * 100).round(1)
    
    # Add spots_left column to show the UI value
    daily_counts['Spots_Left'] = OASIS_CAPACITY - daily_counts['Used_Spots']
    
    # Rename for consistency with other functions
    daily_counts = daily_counts.rename(columns={'Used_Spots': 'People_Count'})
    
    # Sort by date to show in chronological order
    daily_counts = daily_counts.sort_values('Date')
    
    # Calculate average utilization
    avg_utilization = daily_counts['Utilization'].mean()
    
    return daily_counts, avg_utilization

def get_corrected_oasis_utilization(df, weeks_back=8):
    """Calculate historical Oasis utilization per day - matches UI logic exactly (spots used = capacity - spots left)"""
    if df.empty:
        return pd.DataFrame(), 0.0
    
    # Filter for Oasis allocations only
    oasis_df = df[df['Room_Type'] == 'Oasis']
    
    if oasis_df.empty:
        return pd.DataFrame(), 0.0
    
    # NOTE: To match UI behavior exactly, we don't filter by confirmed status
    # The UI "spots left" calculation uses all allocations from weekly_allocations table
    
    # Replicate UI calculation exactly: count unique Team (people) per Date
    daily_counts = oasis_df.groupby(['Date', 'WeekDay'])['Team'].nunique().reset_index()
    daily_counts.columns = ['Date', 'WeekDay', 'Used_Spots']
    
    # Calculate utilization: Used_Spots / OASIS_CAPACITY * 100 (matches UI logic)
    daily_counts['Utilization'] = (daily_counts['Used_Spots'] / OASIS_CAPACITY * 100).round(1)
    
    # Add spots_left column to show the UI value
    daily_counts['Spots_Left'] = OASIS_CAPACITY - daily_counts['Used_Spots']
      # Rename for consistency with other functions
    daily_counts = daily_counts.rename(columns={'Used_Spots': 'People_Count'})
    
    # Calculate average utilization
    avg_utilization = daily_counts['Utilization'].mean()
    
    return daily_counts, avg_utilization

# --- Streamlit App ---
st.title("📊 Historical Data & Analytics")

# Check if confirmed column exists and show setup message if needed
pool = get_db_connection_pool()
if pool:
    conn = get_connection(pool)
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'weekly_allocations' AND column_name = 'confirmed'")
                has_confirmed_col = cur.fetchone() is not None
                
                if not has_confirmed_col:
                    st.warning("⚠️ **Database Setup Required**: The confirmation tracking feature is not yet enabled. To track confirmed vs unconfirmed Oasis attendance:")
                    st.info("1. Run the SQL commands in `backup_tables.sql` on your Supabase database\n2. This will add confirmation tracking to distinguish between allocated and confirmed attendees")
                    st.markdown("---")
        finally:
            return_connection(pool, conn)

pool = get_db_connection_pool()

# Admin authentication
with st.expander("🔐 Admin Access Required"):
    pwd = st.text_input("Enter admin password:", type="password", key="admin_pwd_analytics")
    
    if pwd != RESET_PASSWORD and pwd:
        st.error("❌ Incorrect password.")
        st.stop()
    elif pwd == RESET_PASSWORD:
        st.success("✅ Access granted.")
    else:
        st.info("Please enter admin password to view analytics.")
        st.stop()

# Main analytics interface
st.header("📈 Usage Analytics Dashboard")

# Data source and view selector
st.info("📊 **Data Sources**: This dashboard shows both **current week allocations** (active) and **archived historical data** (from previous weeks).")

col1, col2, col3 = st.columns(3)
with col1:
    weeks_back = st.selectbox("Historical Analysis Period", [4, 8, 12, 24, 52], index=2, key="weeks_selector")
with col2:
    include_current = st.toggle("Include Current Week", value=True, key="include_current")
with col3:
    st.metric("Analyzing Last", f"{weeks_back} weeks", "Historical data" + (" + Current" if include_current else ""))

# Get data
with st.spinner("Loading analytics data..."):
    # Get current week data
    current_df = get_current_allocations(pool)
    
    # Get combined data for analysis
    combined_df = get_combined_allocations(pool, 
                                         date.today() - timedelta(weeks=weeks_back), 
                                         date.today(), 
                                         include_current)
      # Get statistics
    stats = get_usage_statistics(pool, weeks_back)
    daily_util = get_daily_utilization(pool, weeks_back)    # Get additional data for analysis
    historical_df = get_historical_allocations(pool, 
                                              date.today() - timedelta(weeks=weeks_back), 
                                              date.today())
    room_util = get_room_utilization(pool, weeks_back)
    weekly_trends = get_weekly_trends(pool, weeks_back)
    preferences = get_preferences_data(pool, weeks_back)    # Get corrected Oasis utilization
    ui_week_dates = get_ui_week_dates()  # Get exact UI week dates
    current_oasis_daily, current_oasis_avg = get_current_oasis_utilization(current_df, ui_week_dates)
    historical_oasis_daily, historical_oasis_avg = get_corrected_oasis_utilization(historical_df, weeks_back)
    oasis_daily_breakdown = get_oasis_daily_breakdown(pool, weeks_back)

# Get UI week dates for debug comparisons
ui_week_dates = get_ui_week_dates()

# Debug section to show actual data vs UI comparison
with st.expander("🐛 Debug: Raw Data Analysis", expanded=False):
    st.write("**Raw Current Week Oasis Data vs Analytics Calculation**")
    
    # Show UI week dates being used
    st.write(f"**UI Week Dates (from session state)**: {[d.strftime('%Y-%m-%d (%A)') for d in ui_week_dates]}")
    
    if not current_df.empty:
        current_oasis_raw = current_df[current_df['Room_Type'] == 'Oasis'].copy()
        if not current_oasis_raw.empty:
            st.write(f"**Total Oasis records in current week**: {len(current_oasis_raw)}")
            st.write(f"**OASIS_CAPACITY**: {OASIS_CAPACITY}")
              # Show which dates are in the raw data vs UI dates
            raw_dates = sorted(current_oasis_raw['Date'].dt.date.unique())
            # Handle both date and datetime objects
            if hasattr(ui_week_dates[0], 'date'):
                # datetime objects
                ui_dates = [d.date() for d in ui_week_dates]
            else:
                # already date objects
                ui_dates = ui_week_dates
            st.write(f"**Raw data dates**: {[d.strftime('%Y-%m-%d') for d in raw_dates]}")
            st.write(f"**UI dates**: {[d.strftime('%Y-%m-%d') for d in ui_dates]}")
            
            # Show daily breakdown calculation that matches UI
            daily_counts_debug = current_oasis_raw.groupby(['Date', 'WeekDay'])['Team'].nunique().reset_index()
            daily_counts_debug.columns = ['Date', 'WeekDay', 'Unique_People']
            daily_counts_debug['Spots_Left'] = OASIS_CAPACITY - daily_counts_debug['Unique_People']
            daily_counts_debug['Calculated_Utilization'] = (daily_counts_debug['Unique_People'] / OASIS_CAPACITY * 100).round(1)
            daily_counts_debug['Date_Formatted'] = daily_counts_debug['Date'].dt.strftime('%Y-%m-%d')
            
            st.write("**This should match the UI 'spots left' calculation**:")
            st.dataframe(daily_counts_debug[['Date_Formatted', 'WeekDay', 'Unique_People', 'Spots_Left', 'Calculated_Utilization']], 
                        use_container_width=True, hide_index=True)
            
            # Show some raw data
            st.write("**Sample raw allocation records**:")
            sample_data = current_oasis_raw[['Team', 'Date', 'WeekDay', 'Confirmed']].head(10)
            st.dataframe(sample_data, use_container_width=True, hide_index=True)
        else:
            st.info("No current Oasis data found")
    else:
        st.info("No current week data found")

# Display key metrics
if stats:
    st.subheader("📊 Key Metrics Overview")
    
    # Overall metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Allocations", stats['total_allocations'])
    with col2:
        st.metric("Current Project Preferences", stats['current_project_preferences'])
    with col3:
        st.metric("Current Oasis Preferences", stats['current_oasis_preferences'])
    with col4:
        st.metric("Analysis Period", f"{weeks_back} weeks")
    
    # Separated metrics
    st.subheader("🏢 Project Rooms vs 🌿 Oasis Breakdown")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("**Project Rooms**")
        st.metric("Project Allocations", stats['project_allocations'])
        st.metric("Unique Teams", stats['unique_teams'])
        st.metric("Project Rooms Used", stats['unique_project_rooms'])
        st.metric("Most Popular Room", stats['most_popular_project_room'])
        st.metric("Most Popular Day", stats['most_popular_day_projects'])
        
    with col2:
        st.success("**Oasis**")
        st.metric("Oasis Allocations", stats['oasis_allocations'])
        st.metric("Unique Oasis Users", stats['unique_oasis_users'])
        st.metric("Most Popular Day", stats['most_popular_day_oasis'])
        if stats['oasis_allocations'] > 0:
            oasis_percentage = (stats['oasis_allocations'] / stats['total_allocations'] * 100)
            st.metric("% of Total Usage", f"{oasis_percentage:.1f}%")

# Current Week Overview
if not current_df.empty:
    st.subheader("📅 Current Week Status")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        # Count all Oasis entries to match UI logic (spots left calculation)
        current_project = len(current_df[current_df['Room_Type'] == 'Project Room'])
        current_oasis_all = len(current_df[current_df['Room_Type'] == 'Oasis'])
        current_total = current_project + current_oasis_all
        st.metric("Current Allocations", current_total)
    
    with col2:
        st.metric("Project Room Allocations", current_project)
    
    with col3:
        st.metric("Oasis Allocations", current_oasis_all)
    
    with col4:
        # Count teams from both project rooms and confirmed Oasis
        project_teams = set(current_df[current_df['Room_Type'] == 'Project Room']['Team'].unique())
        oasis_teams = set(current_df[(current_df['Room_Type'] == 'Oasis') & (current_df['Confirmed'] == True)]['Team'].unique())
        current_teams = len(project_teams.union(oasis_teams))
        st.metric("Active Teams", current_teams)
      # Current week utilization
    if not current_df.empty:
        st.write("**Current Week Utilization**")
        
        # Calculate current utilization
        current_project_rooms = len(current_df[current_df['Room_Type'] == 'Project Room']['Room'].unique())
        current_project_util = (current_project_rooms / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0        # Calculate Oasis utilization exactly like UI does:
        # UI logic: used_spots = df["Name"].nunique() per day, then utilization = used_spots/capacity * 100
        oasis_data = current_df[current_df['Room_Type'] == 'Oasis']
        if not oasis_data.empty:
            # Count unique people per day (matches UI calculation exactly)
            # Process ALL oasis data, not just one week
            unique_people_per_day = oasis_data.groupby('Date')['Team'].nunique()
            avg_daily_people = unique_people_per_day.mean() if len(unique_people_per_day) > 0 else 0
            current_oasis_util = (avg_daily_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
        else:
            current_oasis_util = 0
            avg_daily_people = 0
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Project Rooms Utilization", f"{current_project_util:.1f}%", 
                     f"{current_project_rooms}/{TOTAL_PROJECT_ROOMS} rooms")
        
        with col2:
            st.metric("Oasis Utilization", f"{current_oasis_util:.1f}%", 
                     f"Avg {avg_daily_people:.1f} people/day")            # Show daily breakdown for current week
            if not current_oasis_daily.empty:
                with st.expander("📊 Current Week Daily Breakdown"):
                    st.info("🔍 **Debug Info**: This uses the same logic as the UI 'spots left' calculation: unique people per day from weekly_allocations table (all allocations, not just confirmed)")
                    
                    # Add a formatted date column for clarity
                    display_df = current_oasis_daily.copy()
                    display_df['Date_Formatted'] = display_df['Date'].dt.strftime('%Y-%m-%d')
                    
                    # Add spots_left column to match UI display
                    if 'Spots_Left' in display_df.columns:
                        st.dataframe(display_df[['Date_Formatted', 'WeekDay', 'People_Count', 'Spots_Left', 'Utilization']], 
                                   use_container_width=True, hide_index=True)
                    else:
                        st.dataframe(display_df[['Date_Formatted', 'WeekDay', 'People_Count', 'Utilization']], 
                                   use_container_width=True, hide_index=True)
    
    # Current week schedule
    with st.expander("📋 View Current Week Schedule"):        # Separate project and oasis data
        current_project = current_df[current_df['Room_Type'] == 'Project Room']
        current_oasis = current_df[current_df['Room_Type'] == 'Oasis']
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Project Rooms**")
            if not current_project.empty:
                pivot_current = current_project.pivot_table(
                    index='Room', 
                    columns='WeekDay', 
                    values='Team', 
                    aggfunc='first',
                    fill_value='Available'
                )
                day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                pivot_current = pivot_current.reindex(columns=[d for d in day_order if d in pivot_current.columns])
                st.dataframe(pivot_current, use_container_width=True)
            else:
                st.info("No current project room allocations.")
        
        with col2:
            st.write("**Oasis**")
            if not current_oasis.empty:
                oasis_current = current_oasis.groupby('WeekDay')['Team'].nunique().reset_index()
                oasis_current.columns = ['Day', 'People']
                oasis_current['Utilization'] = (oasis_current['People'] / OASIS_CAPACITY * 100).round(1)
                st.dataframe(oasis_current, use_container_width=True)
            else:
                st.info("No current Oasis allocations.")

else:
    st.info("No current week allocations found.")

# Room Utilization Charts
st.subheader("🏢 Room Utilization Analysis")

col1, col2 = st.columns(2)

with col1:
    st.write("**Project Rooms Utilization**")
    if not room_util['project'].empty:
        fig_project = px.bar(room_util['project'], x='Room', y='Utilization_Rate',
                            title=f"Project Room Utilization Over Last {weeks_back} Weeks",
                            labels={'Utilization_Rate': 'Utilization (%)', 'Room': 'Room Name'},
                            color='Utilization_Rate',
                            color_continuous_scale='RdYlGn')
        fig_project.update_layout(height=400)
        st.plotly_chart(fig_project, use_container_width=True)
        
        st.dataframe(room_util['project'], use_container_width=True, hide_index=True)
    else:
        st.info("No project room data available for the selected period.")
    
    with col2:
        st.write("**Oasis Utilization**")
        
        # Calculate historical Oasis utilization using the same logic as UI
        # Safety check: ensure historical_df has the required columns
        if not historical_df.empty and 'Room_Type' in historical_df.columns:
            historical_oasis = historical_df[historical_df['Room_Type'] == 'Oasis']
            if not historical_oasis.empty:
                # Count unique people per day (matches UI "used_spots" calculation)
                daily_unique_people = historical_oasis.groupby('Date')['Team'].nunique()
                historical_oasis_avg = (daily_unique_people.mean() / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
            else:
                historical_oasis_avg = 0.0
        else:
            historical_oasis_avg = 0.0
            if historical_df.empty:
                st.info("No historical data available")
            else:
                st.warning(f"Missing columns in historical data: {list(historical_df.columns)}")
            historical_oasis_avg = 0
        
        if historical_oasis_avg > 0:
            # Create a gauge-like visualization for Oasis using corrected calculation
            fig_oasis = go.Figure(go.Indicator(
                mode = "gauge+number+delta",
                value = historical_oasis_avg,
                domain = {'x': [0, 1], 'y': [0, 1]},
                title = {'text': "Avg Daily Oasis Utilization %"},
                delta = {'reference': 75},
                gauge = {'axis': {'range': [None, 100]},
                         'bar': {'color': "darkblue"},
                         'steps': [
                             {'range': [0, 50], 'color': "lightgray"},
                             {'range': [50, 80], 'color': "yellow"},
                             {'range': [80, 100], 'color': "red"}],
                         'threshold': {'line': {'color': "red", 'width': 4},
                                       'thickness': 0.75, 'value': 90}}))
            
            fig_oasis.update_layout(height=300)
            st.plotly_chart(fig_oasis, use_container_width=True)
            
            # Show corrected Oasis statistics using the new calculation
            st.write("**Historical Oasis Statistics:**")
            st.metric("Average Daily Utilization", f"{historical_oasis_avg:.1f}%")
            if not historical_oasis.empty:
                daily_unique_people = historical_oasis.groupby('Date')['Team'].nunique()
                total_days = len(daily_unique_people)
                max_daily_people = daily_unique_people.max()
                max_daily_util = (max_daily_people / OASIS_CAPACITY * 100) if OASIS_CAPACITY > 0 else 0
                st.metric("Total Days with Data", total_days)
                st.metric("Peak Day Utilization", f"{max_daily_util:.1f}%")
        else:
            st.info("No Oasis data available for the selected period.")

# Daily Oasis Breakdown (using corrected data)
if not historical_oasis_daily.empty:
    st.subheader("🌿 Daily Oasis Utilization Breakdown")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Create daily utilization chart
        fig_daily = px.bar(historical_oasis_daily, 
                          x='Date', 
                          y='People_Count',
                          title=f"Daily Oasis Attendance Over Last {weeks_back} Weeks",
                          labels={'People_Count': 'Number of People', 'Date': 'Date'},
                          hover_data=['WeekDay', 'Utilization'])
          # Add capacity line
        fig_daily.add_hline(y=OASIS_CAPACITY, line_dash="dash", line_color="red",
                           annotation_text=f"Capacity ({OASIS_CAPACITY})")
        
        fig_daily.update_layout(height=400, xaxis_tickangle=-45)
        st.plotly_chart(fig_daily, use_container_width=True)
    
    with col2:
        st.write("**Daily Statistics**")
        avg_daily_util = historical_oasis_daily['Utilization'].mean()
        max_daily_util = historical_oasis_daily['Utilization'].max()
        min_daily_util = historical_oasis_daily['Utilization'].min()
        
        st.metric("Average Daily Utilization", f"{avg_daily_util:.1f}%")
        st.metric("Highest Daily Utilization", f"{max_daily_util:.1f}%")
        st.metric("Lowest Daily Utilization", f"{min_daily_util:.1f}%")
        
        # Show top 5 busiest days
        st.write("**Top 5 Busiest Days**")
        top_days = historical_oasis_daily.nlargest(5, 'People_Count')[['Date', 'WeekDay', 'People_Count', 'Utilization']]
        st.dataframe(top_days, use_container_width=True, hide_index=True)
    
    # Detailed daily breakdown table
    with st.expander("📊 View Complete Daily Breakdown"):
        st.dataframe(historical_oasis_daily, use_container_width=True, hide_index=True)

# Weekly Trends
if not weekly_trends.empty:
    st.subheader("📈 Weekly Allocation Trends")
    
    fig_trends = go.Figure()
    fig_trends.add_trace(go.Scatter(x=weekly_trends['Week'], y=weekly_trends['Total_Allocations'],
                                    mode='lines+markers', name='Total Allocations'))
    fig_trends.add_trace(go.Scatter(x=weekly_trends['Week'], y=weekly_trends['Rooms_Used'],
                                    mode='lines+markers', name='Rooms Used', yaxis='y2'))
    
    fig_trends.update_layout(
        title=f"Allocation Trends Over Last {weeks_back} Weeks",
        xaxis_title="Week Starting",
        yaxis_title="Total Allocations",
        yaxis2=dict(title="Rooms Used", overlaying='y', side='right'),
        height=400
    )
    st.plotly_chart(fig_trends, use_container_width=True)

# Day of Week Analysis
if not historical_df.empty:
    st.subheader("📅 Day of Week Popularity")
    
    day_popularity = historical_df['WeekDay'].value_counts().reset_index()
    day_popularity.columns = ['Day', 'Count']
    
    # Reorder by weekday
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    day_popularity['Day'] = pd.Categorical(day_popularity['Day'], categories=day_order, ordered=True)
    day_popularity = day_popularity.sort_values('Day')
    
    fig_days = px.pie(day_popularity, values='Count', names='Day',
                      title="Allocation Distribution by Day of Week")
    st.plotly_chart(fig_days, use_container_width=True)

# Team Activity Analysis
if not historical_df.empty:
    st.subheader("👥 Most Active Teams")
    
    project_df = historical_df[historical_df['Room_Type'] == 'Project Room']
    oasis_df = historical_df[historical_df['Room_Type'] == 'Oasis']
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Most Active Project Teams**")
        if not project_df.empty:
            team_activity = project_df['Team'].value_counts().head(10).reset_index()
            team_activity.columns = ['Team', 'Allocations']
            
            fig_teams = px.bar(team_activity, x='Allocations', y='Team', orientation='h',
                              title="Top 10 Most Active Project Teams",
                              labels={'Allocations': 'Number of Allocations', 'Team': 'Team Name'})
            fig_teams.update_layout(height=400)
            st.plotly_chart(fig_teams, use_container_width=True)
        else:
            st.info("No project team data available.")
            
    with col2:
        st.write("**Most Active Oasis Users**")
        if not oasis_df.empty:
            oasis_activity = oasis_df['Team'].value_counts().head(10).reset_index()
            oasis_activity.columns = ['User', 'Allocations']
            
            fig_oasis_users = px.bar(oasis_activity, x='Allocations', y='User', orientation='h',
                                    title="Top 10 Most Active Oasis Users",
                                    labels={'Allocations': 'Number of Allocations', 'User': 'User Name'})
            fig_oasis_users.update_layout(height=400)
            st.plotly_chart(fig_oasis_users, use_container_width=True)
        else:
            st.info("No Oasis user data available.")

# Current Status and Data Management
st.subheader("📋 Current Data Status & Management")

col1, col2 = st.columns(2)

with col1:
    st.write("**Current Preferences (Active)**")
    if not preferences['project'].empty:
        st.info(f"🏢 **Project Teams**: {len(preferences['project'])} active preferences")
        with st.expander("View Project Preferences"):
            st.dataframe(preferences['project'][['Team', 'Contact', 'Size', 'Preferred_Days']], 
                        use_container_width=True, hide_index=True)
    else:
        st.warning("No active project preferences")
        
    if not preferences['oasis'].empty:
        st.info(f"🌿 **Oasis Users**: {len(preferences['oasis'])} active preferences")
        with st.expander("View Oasis Preferences"):
            st.dataframe(preferences['oasis'][['Person', 'Day1', 'Day2', 'Day3', 'Day4', 'Day5']], 
                        use_container_width=True, hide_index=True)
    else:
        st.warning("No active Oasis preferences")

with col2:
    st.write("**Data Management Info**")
    st.info("""
    **How Data Transitions Work:**
    
    🔄 **Weekly Cycle:**
    - Preferences collected during submission period
    - Allocations generated from preferences
    - Data archived before deletion (backup system)
    
    🗃️ **Backup System:**
    - All deletions are backed up to archive tables
    - Historical data preserved for analysis
    - Admin can restore if needed
    
    📊 **Analytics:**
    - Project rooms and Oasis analyzed separately
    - Utilization calculated differently for each type
    - Current preferences vs historical allocations
    """)

    if st.button("🔍 Check Archive Tables"):
        # Check if archive tables have data
        conn = get_connection(pool)
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM weekly_preferences_archive")
                    archived_prefs = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM oasis_preferences_archive")
                    archived_oasis = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM weekly_allocations_archive")
                    archived_allocs = cur.fetchone()[0]
                    
                    st.success(f"""
                    **Archive Status:**
                    - Project Preferences: {archived_prefs} archived
                    - Oasis Preferences: {archived_oasis} archived
                    - Allocations: {archived_allocs} archived
                    """)
            except Exception as e:
                st.error(f"Error checking archives: {e}")
            finally:
                return_connection(pool, conn)

# Historical Data Browser
st.subheader("🗓️ Historical Data Browser")

if not historical_df.empty:
    # Week selector for historical view
    available_weeks = sorted(historical_df['WeekStart'].dt.date.unique(), reverse=True)
    
    selected_week = st.selectbox("Select Week to View", 
                                options=available_weeks,
                                format_func=lambda x: f"Week of {x}",
                                key="week_browser")
    
    if selected_week:
        week_data = historical_df[historical_df['WeekStart'].dt.date == selected_week]
        
        if not week_data.empty:
            st.write(f"**Showing allocations for week of {selected_week}**")
            
            # Create a pivot table for better visualization
            pivot_data = week_data.pivot_table(
                index='Room', 
                columns='WeekDay', 
                values='Team', 
                aggfunc='first',
                fill_value='Vacant'
            )
            
            # Reorder columns by weekday
            day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
            pivot_data = pivot_data.reindex(columns=[d for d in day_order if d in pivot_data.columns])
            
            st.dataframe(pivot_data, use_container_width=True)
        else:
            st.info("No allocations found for the selected week.")

# Export functionality
st.subheader("📤 Export Historical Data")

if not historical_df.empty:
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Export Analytics Summary"):
            summary_data = {
                'Metric': ['Total Allocations', 'Unique Teams', 'Unique Rooms', 'Most Popular Room', 'Most Popular Day'],
                'Value': [stats['total_allocations'], stats['unique_teams'], stats['unique_rooms'], 
                         stats['most_popular_room'], stats['most_popular_day']]
            }
            summary_df = pd.DataFrame(summary_data)
            csv = summary_df.to_csv(index=False)
            st.download_button(
                label="Download Analytics Summary",
                data=csv,
                file_name=f"room_analytics_summary_{date.today()}.csv",
                mime="text/csv"
            )
    
    with col2:
        if st.button("📋 Export Raw Historical Data"):
            export_df = historical_df[['Date', 'Team', 'Room', 'WeekDay']].copy()
            export_df['Date'] = export_df['Date'].dt.strftime('%Y-%m-%d')
            csv = export_df.to_csv(index=False)
            st.download_button(
                label="Download Historical Data",
                data=csv,
                file_name=f"room_allocations_history_{date.today()}.csv",
                mime="text/csv"
            )

# Data insights
if stats:
    st.subheader("💡 Insights & Recommendations")
    
    insights = []
    
    # Utilization insights
    if not daily_util.empty:
        avg_project_util = daily_util['Project_Utilization'].mean()
        avg_oasis_util = daily_util['Oasis_Utilization'].mean()
        
        if avg_project_util > 80:
            insights.append(f"🔥 **High Project Room Demand**: {avg_project_util:.1f}% average utilization - rooms are in high demand")
        elif avg_project_util < 30:
            insights.append(f"📉 **Low Project Room Usage**: {avg_project_util:.1f}% average utilization - rooms are underutilized")
        else:
            insights.append(f"✅ **Balanced Project Room Usage**: {avg_project_util:.1f}% average utilization")
            
        if avg_oasis_util > 80:
            insights.append(f"🌿 **Oasis High Demand**: {avg_oasis_util:.1f}% average utilization - consider expanding capacity")
        elif avg_oasis_util < 30:
            insights.append(f"🌿 **Oasis Low Usage**: {avg_oasis_util:.1f}% average utilization - promote Oasis benefits")
        else:
            insights.append(f"🌿 **Oasis Balanced Usage**: {avg_oasis_util:.1f}% average utilization")
    
    # Day preferences insights
    if not historical_df.empty:
        project_df = historical_df[historical_df['Room_Type'] == 'Project Room']
        oasis_df = historical_df[historical_df['Room_Type'] == 'Oasis']
        
        if not project_df.empty:
            most_popular_project_day = project_df['WeekDay'].mode().iloc[0] if len(project_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"📅 **Project Peak Day**: {most_popular_project_day} is most requested for project rooms")
            
        if not oasis_df.empty:
            most_popular_oasis_day = oasis_df['WeekDay'].mode().iloc[0] if len(oasis_df['WeekDay'].mode()) > 0 else "N/A"
            insights.append(f"📅 **Oasis Peak Day**: {most_popular_oasis_day} is most popular for Oasis")
    
    # Preference vs allocation insights
    if stats['current_project_preferences'] == 0:
        insights.append("⚠️ **No Active Project Preferences**: Teams may need to submit preferences for upcoming week")
    
    if stats['current_oasis_preferences'] == 0:
        insights.append("⚠️ **No Active Oasis Preferences**: Individuals may need to submit Oasis preferences")
    
    # Usage balance
    if stats['total_allocations'] > 0:
        project_ratio = (stats['project_allocations'] / stats['total_allocations']) * 100
        oasis_ratio = (stats['oasis_allocations'] / stats['total_allocations']) * 100
        insights.append(f"⚖️ **Usage Split**: {project_ratio:.1f}% Project Rooms, {oasis_ratio:.1f}% Oasis")
    
    for insight in insights:
        st.info(insight)

else:
    st.info("No historical data available. Allocations will appear here once the system has been used.")

# DEBUG: Oasis Utilization Calculation Analysis
st.subheader("🔍 DEBUG: Oasis Utilization Analysis")
st.info("This section shows exactly how Oasis utilization is calculated to match the UI.")

# Show the current week date range being used
today = date.today()
days_since_monday = today.weekday()
monday_this_week = today - timedelta(days=days_since_monday)
friday_this_week = monday_this_week + timedelta(days=4)
st.write(f"**Current calendar week**: {monday_this_week} to {friday_this_week}")

# Show what data we actually have
if not current_df.empty:
    st.write(f"**Total records in weekly_allocations**: {len(current_df)}")
    date_range = f"{current_df['Date'].min().strftime('%Y-%m-%d')} to {current_df['Date'].max().strftime('%Y-%m-%d')}"
    st.write(f"**Actual date range in data**: {date_range}")
else:
    st.warning("**No data found in weekly_allocations table**")

if not current_df.empty:
    oasis_current_raw = current_df[current_df['Room_Type'] == 'Oasis']
    
    # Initialize display_debug outside the nested if block to avoid scope issues
    display_debug = pd.DataFrame()
    
    if not oasis_current_raw.empty:
        st.write("**Current Week Oasis Raw Data:**")
        st.write(f"**Date range in data**: {oasis_current_raw['Date'].min().strftime('%Y-%m-%d')} to {oasis_current_raw['Date'].max().strftime('%Y-%m-%d')}")
        
        # Show which weeks this data spans
        unique_weeks = oasis_current_raw['WeekStart'].unique()
        week_strings = [w.strftime('%Y-%m-%d') for w in sorted(unique_weeks)]
        st.write(f"**Weeks included**: {', '.join(week_strings)}")
        
        st.dataframe(oasis_current_raw[['Team', 'Date', 'WeekDay', 'Confirmed']], use_container_width=True)
        
        # DEBUG: Check for duplicate records
        st.write("**Debug: Check for duplicates in analytics data source:**")
        duplicate_check = oasis_current_raw.groupby(['Team', 'Date']).size().reset_index(name='Count')
        duplicates = duplicate_check[duplicate_check['Count'] > 1]
        if not duplicates.empty:
            st.error("⚠️ **FOUND DUPLICATES!** This explains the double counting:")
            st.dataframe(duplicates, use_container_width=True)
        else:
            st.success("✅ No duplicates found in raw data")
        
        # DEBUG: Show unique people per day from raw data
        st.write("**Debug: Manual count of unique people per day from raw data:**")
        manual_count = oasis_current_raw.groupby('Date')['Team'].nunique().reset_index()
        manual_count.columns = ['Date', 'Manual_Unique_Count']
        manual_count['Date_Str'] = manual_count['Date'].dt.strftime('%Y-%m-%d')
        st.dataframe(manual_count[['Date_Str', 'Manual_Unique_Count']], use_container_width=True)
        
        # Show UI calculation logic step by step
        st.write("**UI Logic Replication:**")
        
        # Group by date and count unique people (exactly like UI)
        daily_counts_debug = oasis_current_raw.groupby('Date')['Team'].nunique().reset_index()
        daily_counts_debug.columns = ['Date', 'Used_Spots']
        daily_counts_debug['Spots_Left'] = OASIS_CAPACITY - daily_counts_debug['Used_Spots']
        daily_counts_debug['Utilization_Percent'] = (daily_counts_debug['Used_Spots'] / OASIS_CAPACITY * 100).round(1)
        daily_counts_debug['WeekDay'] = daily_counts_debug['Date'].dt.day_name()
        
        st.write("**Daily Breakdown (Matches UI 'spots left' logic):**")
        display_debug = daily_counts_debug[['Date', 'WeekDay', 'Used_Spots', 'Spots_Left', 'Utilization_Percent']].copy()
        display_debug['Date'] = display_debug['Date'].dt.strftime('%Y-%m-%d')
        st.dataframe(display_debug, use_container_width=True, hide_index=True)        # Show the analytics calculation for comparison
        st.write("**Analytics Function Result vs Manual Calculation:**")
        try:
            current_oasis_daily_analytics, current_oasis_avg_analytics = get_current_oasis_utilization(current_df, ui_week_dates)
            
            if not current_oasis_daily_analytics.empty:
                st.write("**Analytics Function Output:**")
                analytics_display = current_oasis_daily_analytics.copy()
                analytics_display['Date_Formatted'] = analytics_display['Date'].dt.strftime('%Y-%m-%d')
                st.dataframe(analytics_display[['Date_Formatted', 'WeekDay', 'People_Count', 'Utilization']], 
                            use_container_width=True, hide_index=True)
            else:
                st.warning("Analytics function returned empty DataFrame")
        except Exception as e:
            st.error(f"Error in analytics function: {e}")
            current_oasis_daily_analytics = pd.DataFrame()
            current_oasis_avg_analytics = 0.0
            
        # DEBUG: Step-by-step analytics function debugging
        st.write("**🔍 Step-by-step Analytics Function Debug:**")
        
        # Replicate the analytics function step by step
        oasis_df_step1 = current_df[current_df['Room_Type'] == 'Oasis']
        st.write(f"Step 1 - Filter Oasis: {len(oasis_df_step1)} records")
        
        if not oasis_df_step1.empty:
            latest_week = oasis_df_step1['WeekStart'].max()
            st.write(f"Step 2 - Latest week found: {latest_week}")
            
            oasis_df_step2 = oasis_df_step1[oasis_df_step1['WeekStart'] == latest_week]
            st.write(f"Step 3 - Filter to latest week: {len(oasis_df_step2)} records")
            
            # Show sample of this filtered data
            st.write("**Sample of filtered data used by analytics:**")
            sample_analytics = oasis_df_step2[['Team', 'Date', 'WeekDay', 'WeekStart']].head(10)
            st.dataframe(sample_analytics, use_container_width=True, hide_index=True)
            
            # Show the groupby operation step by step
            daily_counts_step = oasis_df_step2.groupby(['Date', 'WeekDay'])['Team'].nunique().reset_index()
            st.write(f"Step 4 - Groupby result: {len(daily_counts_step)} days")
            st.dataframe(daily_counts_step, use_container_width=True, hide_index=True)        # Compare with manual calculation
        st.write("**🔍 Comparison: Manual vs Analytics:**")
        if not display_debug.empty and 'current_oasis_daily_analytics' in locals() and not current_oasis_daily_analytics.empty:
            comparison = []
            for _, row in display_debug.iterrows():
                date_str = row['Date']  # This should be formatted like '2024-05-27'
                manual_count = row['Used_Spots']
                
                # Find matching analytics result - convert analytics dates to same format
                analytics_match = current_oasis_daily_analytics[
                    current_oasis_daily_analytics['Date'].dt.strftime('%Y-%m-%d') == date_str
                ]
                
                if not analytics_match.empty:
                    analytics_count = analytics_match.iloc[0]['People_Count']
                    difference = analytics_count - manual_count
                else:
                    analytics_count = "No match"
                    difference = "N/A"
                
                comparison.append({
                    'Date': date_str,
                    'Manual_Count': manual_count,
                    'Analytics_Count': analytics_count,
                    'Difference': difference
                })
            
            comparison_df = pd.DataFrame(comparison)
            st.dataframe(comparison_df, use_container_width=True, hide_index=True)
            
            # Show summary of matches vs mismatches
            matches = sum(1 for item in comparison if item['Analytics_Count'] != "No match")
            total = len(comparison)
            st.write(f"**Match Summary**: {matches}/{total} dates matched between manual and analytics")
            
        else:
            st.warning("No data available for comparison - check if there's current Oasis data and analytics function worked")
        if not current_oasis_daily_analytics.empty:
            st.dataframe(current_oasis_daily_analytics[['Date', 'WeekDay', 'People_Count', 'Utilization']], use_container_width=True, hide_index=True)
        else:
            st.warning("Analytics function returned empty data")
          # Calculate overall stats
        if not display_debug.empty:
            avg_utilization_debug = daily_counts_debug['Utilization_Percent'].mean()
            total_used_spots = daily_counts_debug['Used_Spots'].sum()
            total_capacity_days = len(daily_counts_debug) * OASIS_CAPACITY
            overall_utilization = (total_used_spots / total_capacity_days * 100) if total_capacity_days > 0 else 0
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Average Daily Utilization (Debug)", f"{avg_utilization_debug:.1f}%")
            with col2:
                st.metric("Overall Weekly Utilization", f"{overall_utilization:.1f}%")
            with col3:
                st.metric("Total People-Days", total_used_spots)
        else:
            st.info("No data available for utilization calculation")
    else:
        st.warning("No current Oasis data found")
else:
    st.warning("No current week data found")
