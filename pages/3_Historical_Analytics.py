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
    from app import get_db_connection_pool, get_connection, return_connection, AVAILABLE_ROOMS
    # Calculate room counts from rooms.json
    PROJECT_ROOMS = [r for r in AVAILABLE_ROOMS if r["name"] != "Oasis"]
    OASIS_ROOM = next((r for r in AVAILABLE_ROOMS if r["name"] == "Oasis"), {"capacity": 16})
    
    TOTAL_PROJECT_ROOMS = len(PROJECT_ROOMS)
    OASIS_CAPACITY = OASIS_ROOM["capacity"]
    
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

# --- Historical Data Functions ---
def get_historical_allocations(pool, start_date=None, end_date=None):
    """Get historical allocation data from archive table"""
    if not pool: return pd.DataFrame()
    conn = get_connection(pool)
    if not conn: return pd.DataFrame()
    
    try:
        with conn.cursor() as cur:
            if start_date and end_date:
                cur.execute("""
                    SELECT team_name, room_name, date, 
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations_archive 
                    WHERE date >= %s AND date <= %s
                    ORDER BY date DESC
                """, (start_date, end_date))
            else:
                cur.execute("""
                    SELECT team_name, room_name, date,
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations_archive 
                    ORDER BY date DESC
                    LIMIT 1000
                """)
            
            rows = cur.fetchall()
            if not rows:
                return pd.DataFrame()
                
            df = pd.DataFrame(rows, columns=["Team", "Room", "Date", "DayOfWeek", "WeekNumber", "Year"])
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

def get_usage_statistics(pool, weeks_back=12):
    """Get usage statistics separated by room type"""
    if not pool: return {}
    
    conn = get_connection(pool)
    if not conn: return {}
    
    try:
        with conn.cursor() as cur:
            # Get archived allocations statistics
            end_date = date.today()
            start_date = end_date - timedelta(weeks=weeks_back)
            
            cur.execute("""
                SELECT room_name, team_name, date
                FROM weekly_allocations_archive 
                WHERE date >= %s AND date <= %s
            """, (start_date, end_date))
            
            rows = cur.fetchall()
            if not rows:
                return {}
            
            df = pd.DataFrame(rows, columns=["Room", "Team", "Date"])
            df['Room_Type'] = df['Room'].apply(lambda x: 'Oasis' if x == 'Oasis' else 'Project Room')
            
            # Calculate separated statistics
            project_df = df[df['Room_Type'] == 'Project Room']
            oasis_df = df[df['Room_Type'] == 'Oasis']
            
            # Get current preferences
            cur.execute("SELECT COUNT(*) FROM weekly_preferences")
            current_project_prefs = cur.fetchone()[0] or 0
            
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            current_oasis_prefs = cur.fetchone()[0] or 0
            
            stats = {
                'total_allocations': len(df),
                'project_allocations': len(project_df),
                'oasis_allocations': len(oasis_df),
                'unique_teams': df['Team'].nunique(),
                'unique_project_rooms': project_df['Room'].nunique() if not project_df.empty else 0,
                'date_range': f"{df['Date'].min()} to {df['Date'].max()}" if not df.empty else "No data",
                'current_project_preferences': current_project_prefs,
                'current_oasis_preferences': current_oasis_prefs,
                'most_popular_room': df['Room'].mode().iloc[0] if len(df['Room'].mode()) > 0 else "N/A",
                'most_popular_day': pd.to_datetime(df['Date']).dt.day_name().mode().iloc[0] if not df.empty else "N/A",
                'most_active_team': df['Team'].mode().iloc[0] if len(df['Team'].mode()) > 0 else "N/A"
            }
            
            return stats
            
    except Exception as e:
        st.error(f"Failed to fetch usage statistics: {e}")
        return {}
    finally:
        return_connection(pool, conn)

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
        project_utilization = (project_rooms_used / TOTAL_PROJECT_ROOMS * 100) if TOTAL_PROJECT_ROOMS > 0 else 0
        
        # Oasis utilization (count people, not rooms)
        oasis_people = len(day_data[day_data['Room_Type'] == 'Oasis'])
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

def get_weekly_trends(pool, weeks_back=12):
    """Get weekly allocation trends separated by room type"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Group by week and room type
    weekly_trends = df.groupby(['WeekStart', 'Room_Type']).agg({
        'Team': 'count',
        'Room': 'nunique'
    }).reset_index()
    
    weekly_trends.columns = ['Week', 'Room_Type', 'Total_Allocations', 'Rooms_Used']
    weekly_trends['Week'] = weekly_trends['Week'].dt.strftime('%Y-%m-%d')
    
    return weekly_trends

# --- Streamlit App ---
st.title("📊 Historical Data & Analytics")
st.caption(f"📋 **Room Configuration**: {TOTAL_PROJECT_ROOMS} Project Rooms + Oasis ({OASIS_CAPACITY} people)")

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

# Data source explanation
st.info("📊 **Data Source**: This dashboard analyzes **archived allocation data** that is preserved when weekly resets occur. Current active preferences are also shown for context.")

# Date range selector
col1, col2 = st.columns(2)
with col1:
    weeks_back = st.selectbox("Analysis Period", [4, 8, 12, 24, 52], index=2, key="weeks_selector")
with col2:
    st.metric("Analyzing Last", f"{weeks_back} weeks", "Historical data")

# Get data
with st.spinner("Loading analytics data..."):
    stats = get_usage_statistics(pool, weeks_back)
    daily_util = get_daily_utilization(pool, weeks_back)
    weekly_trends = get_weekly_trends(pool, weeks_back)
    historical_df = get_historical_allocations(pool, 
                                             date.today() - timedelta(weeks=weeks_back), 
                                             date.today())

# Display key metrics
if stats:
    st.subheader("📊 Key Metrics")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Allocations", stats['total_allocations'])
        st.caption("From archived data")
    with col2:
        st.metric("Unique Teams", stats['unique_teams'])
    with col3:
        st.metric("Current Project Preferences", stats['current_project_preferences'])
        st.caption("Active submissions")
    with col4:
        st.metric("Current Oasis Preferences", stats['current_oasis_preferences'])
        st.caption("Active submissions")
        
    # Allocation breakdown
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Project Room Allocations", stats['project_allocations'])
        if stats['total_allocations'] > 0:
            project_percentage = (stats['project_allocations'] / stats['total_allocations'] * 100)
            st.caption(f"{project_percentage:.1f}% of total")
    with col2:
        st.metric("Oasis Allocations", stats['oasis_allocations'])
        if stats['total_allocations'] > 0:
            oasis_percentage = (stats['oasis_allocations'] / stats['total_allocations'] * 100)
            st.caption(f"{oasis_percentage:.1f}% of total")
    with col3:
        st.metric("Most Active Team", stats['most_active_team'])

# Daily Utilization Analysis
if not daily_util.empty:
    st.subheader("📈 Daily Utilization Rates")
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Project Rooms", TOTAL_PROJECT_ROOMS)
    with col2:
        avg_project_util = daily_util['Project_Utilization'].mean()
        st.metric("Avg Project Room Utilization", f"{avg_project_util:.1f}%")
    with col3:
        st.metric("Oasis Capacity", f"{OASIS_CAPACITY} people")
    with col4:
        avg_oasis_util = daily_util['Oasis_Utilization'].mean()
        st.metric("Avg Oasis Utilization", f"{avg_oasis_util:.1f}%")
    
    # Daily utilization by day of week
    st.write("**Average Utilization by Day of Week**")
    
    # Project rooms utilization by day
    project_daily = daily_util.groupby('WeekDay')['Project_Utilization'].mean().reset_index()
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    project_daily['WeekDay'] = pd.Categorical(project_daily['WeekDay'], categories=day_order, ordered=True)
    project_daily = project_daily.sort_values('WeekDay')
    
    # Oasis utilization by day
    oasis_daily = daily_util.groupby('WeekDay')['Oasis_Utilization'].mean().reset_index()
    oasis_daily['WeekDay'] = pd.Categorical(oasis_daily['WeekDay'], categories=day_order, ordered=True)
    oasis_daily = oasis_daily.sort_values('WeekDay')
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_project_daily = px.bar(project_daily, x='WeekDay', y='Project_Utilization',
                                  title="Project Rooms - Daily Average Utilization",
                                  labels={'Project_Utilization': 'Utilization (%)', 'WeekDay': 'Day'},
                                  color='Project_Utilization',
                                  color_continuous_scale='RdYlGn')
        fig_project_daily.update_layout(height=400)
        st.plotly_chart(fig_project_daily, use_container_width=True)
    
    with col2:
        fig_oasis_daily = px.bar(oasis_daily, x='WeekDay', y='Oasis_Utilization',
                                title="Oasis - Daily Average Utilization",
                                labels={'Oasis_Utilization': 'Utilization (%)', 'WeekDay': 'Day'},
                                color='Oasis_Utilization',
                                color_continuous_scale='Blues')
        fig_oasis_daily.update_layout(height=400)
        st.plotly_chart(fig_oasis_daily, use_container_width=True)
    
    # Weekly trends
    st.write("**Weekly Utilization Trends**")
    weekly_util = daily_util.groupby('WeekStart').agg({
        'Project_Utilization': 'mean',
        'Oasis_Utilization': 'mean'
    }).reset_index()
    weekly_util['WeekStart'] = pd.to_datetime(weekly_util['WeekStart'])
    
    fig_weekly_util = go.Figure()
    fig_weekly_util.add_trace(go.Scatter(
        x=weekly_util['WeekStart'], 
        y=weekly_util['Project_Utilization'],
        mode='lines+markers', 
        name=f'Project Rooms (/{TOTAL_PROJECT_ROOMS})',
        line=dict(color='#ff7f0e')
    ))
    fig_weekly_util.add_trace(go.Scatter(
        x=weekly_util['WeekStart'], 
        y=weekly_util['Oasis_Utilization'],
        mode='lines+markers', 
        name=f'Oasis (/{OASIS_CAPACITY} people)',
        line=dict(color='#1f77b4')
    ))
    
    fig_weekly_util.update_layout(
        title="Weekly Average Utilization Trends",
        xaxis_title="Week Starting",
        yaxis_title="Utilization (%)",
        height=400
    )
    st.plotly_chart(fig_weekly_util, use_container_width=True)
    
    # Detailed daily utilization table
    with st.expander("📋 View Detailed Daily Utilization Data"):
        display_df = daily_util[['Date', 'WeekDay', 'Project_Rooms_Used', 'Project_Utilization', 
                                'Oasis_People', 'Oasis_Utilization']].copy()
        display_df.columns = ['Date', 'Day', 'Rooms Used', 'Project %', 'Oasis People', 'Oasis %']
        st.dataframe(display_df, use_container_width=True)

# Team Activity Analysis
if not historical_df.empty:
    st.subheader("👥 Most Active Teams")
    
    team_activity = historical_df['Team'].value_counts().head(10).reset_index()
    team_activity.columns = ['Team', 'Allocations']
    
    fig_teams = px.bar(team_activity, x='Allocations', y='Team', orientation='h',
                       title="Top 10 Most Active Teams",
                       labels={'Allocations': 'Number of Allocations', 'Team': 'Team Name'})
    fig_teams.update_layout(height=400)
    st.plotly_chart(fig_teams, use_container_width=True)

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
            
            # Separate project and oasis data
            project_data = week_data[week_data['Room_Type'] == 'Project Room']
            oasis_data = week_data[week_data['Room_Type'] == 'Oasis']
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Project Rooms**")
                if not project_data.empty:
                    # Create a pivot table for better visualization
                    pivot_data = project_data.pivot_table(
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
                    st.info("No project room allocations for this week.")
            
            with col2:
                st.write("**Oasis**")
                if not oasis_data.empty:
                    oasis_summary = oasis_data.groupby('WeekDay')['Team'].count().reset_index()
                    oasis_summary.columns = ['Day', 'People']
                    oasis_summary['Utilization'] = (oasis_summary['People'] / OASIS_CAPACITY * 100).round(1)
                    st.dataframe(oasis_summary, use_container_width=True)
                else:
                    st.info("No Oasis allocations for this week.")
        else:
            st.info("No allocations found for the selected week.")

# Export functionality
st.subheader("📤 Export Historical Data")

if not historical_df.empty:
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Export Analytics Summary"):
            summary_data = {
                'Metric': ['Total Allocations', 'Project Allocations', 'Oasis Allocations', 'Unique Teams', 'Most Active Team'],
                'Value': [stats['total_allocations'], stats['project_allocations'], stats['oasis_allocations'],
                         stats['unique_teams'], stats['most_active_team']]
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
            export_df = historical_df[['Date', 'Team', 'Room', 'WeekDay', 'Room_Type']].copy()
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

    # Weekly transition insights
    insights.append("🔄 **Weekly Process**: Data is archived when allocations are reset for new weeks, preserving historical trends")
    
    for insight in insights:
        st.info(insight)

else:
    st.info("No historical data available. Allocations will appear here once the system has been used and weekly transitions have occurred.")
