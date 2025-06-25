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
    from app import get_db_connection_pool, get_connection, return_connection
except ImportError:
    st.error("❌ Could not import from main app. Please check file structure.")
    st.stop()

# --- Historical Data Functions ---
def get_historical_allocations(pool, start_date=None, end_date=None):
    """Get historical allocation data"""
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
                    FROM weekly_allocations 
                    WHERE date >= %s AND date <= %s
                    ORDER BY date DESC
                """, (start_date, end_date))
            else:
                cur.execute("""
                    SELECT team_name, room_name, date,
                           EXTRACT(DOW FROM date) as day_of_week,
                           EXTRACT(WEEK FROM date) as week_number,
                           EXTRACT(YEAR FROM date) as year
                    FROM weekly_allocations 
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
            return df
            
    except Exception as e:
        st.error(f"Failed to fetch historical data: {e}")
        return pd.DataFrame()
    finally:
        return_connection(pool, conn)

def get_usage_statistics(pool, weeks_back=12):
    """Get usage statistics for analysis"""
    if not pool: return {}
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return {}
    
    # Calculate statistics
    stats = {
        'total_allocations': len(df),
        'unique_teams': df['Team'].nunique(),
        'unique_rooms': df['Room'].nunique(),
        'date_range': f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}",
        'most_popular_room': df['Room'].mode().iloc[0] if len(df['Room'].mode()) > 0 else "N/A",
        'most_popular_day': df['WeekDay'].mode().iloc[0] if len(df['WeekDay'].mode()) > 0 else "N/A",
        'most_active_team': df['Team'].mode().iloc[0] if len(df['Team'].mode()) > 0 else "N/A"
    }
    
    return stats

def get_room_utilization(pool, weeks_back=8):
    """Calculate room utilization rates"""
    if not pool: return pd.DataFrame()
    
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks_back)
    df = get_historical_allocations(pool, start_date, end_date)
    
    if df.empty:
        return pd.DataFrame()
    
    # Calculate utilization by room
    room_usage = df.groupby('Room').size().reset_index(name='Usage_Count')
    
    # Calculate total possible slots (assuming 4 days per week * weeks_back)
    total_possible_slots = 4 * weeks_back
    room_usage['Utilization_Rate'] = (room_usage['Usage_Count'] / total_possible_slots * 100).round(1)
    
    return room_usage.sort_values('Utilization_Rate', ascending=False)

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

# --- Streamlit App ---
st.title("📊 Historical Data & Analytics")

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

# Date range selector
col1, col2 = st.columns(2)
with col1:
    weeks_back = st.selectbox("Analysis Period", [4, 8, 12, 24, 52], index=2, key="weeks_selector")
with col2:
    st.metric("Analyzing Last", f"{weeks_back} weeks", "Historical data")

# Get data
with st.spinner("Loading analytics data..."):
    stats = get_usage_statistics(pool, weeks_back)
    room_util = get_room_utilization(pool, weeks_back)
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
    with col2:
        st.metric("Unique Teams", stats['unique_teams'])
    with col3:
        st.metric("Most Popular Room", stats['most_popular_room'])
    with col4:
        st.metric("Most Popular Day", stats['most_popular_day'])

# Room Utilization Chart
if not room_util.empty:
    st.subheader("🏢 Room Utilization Rates")
    
    fig_util = px.bar(room_util, x='Room', y='Utilization_Rate',
                      title=f"Room Utilization Over Last {weeks_back} Weeks",
                      labels={'Utilization_Rate': 'Utilization (%)', 'Room': 'Room Name'},
                      color='Utilization_Rate',
                      color_continuous_scale='RdYlGn')
    fig_util.update_layout(height=400)
    st.plotly_chart(fig_util, use_container_width=True)
    
    # Show utilization table
    st.dataframe(room_util, use_container_width=True)

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
    
    if not room_util.empty:
        high_util_rooms = room_util[room_util['Utilization_Rate'] > 75]
        low_util_rooms = room_util[room_util['Utilization_Rate'] < 25]
        
        if not high_util_rooms.empty:
            insights.append(f"🔥 **High Demand**: {', '.join(high_util_rooms['Room'].tolist())} are heavily utilized (>75%)")
        
        if not low_util_rooms.empty:
            insights.append(f"📉 **Low Usage**: {', '.join(low_util_rooms['Room'].tolist())} are underutilized (<25%)")
    
    if not historical_df.empty:
        oasis_usage = len(historical_df[historical_df['Room'] == 'Oasis'])
        total_usage = len(historical_df)
        oasis_percentage = (oasis_usage / total_usage * 100) if total_usage > 0 else 0
        
        insights.append(f"🌿 **Oasis Usage**: {oasis_percentage:.1f}% of all allocations")
        
        if stats['most_popular_day']:
            insights.append(f"📅 **Peak Day**: {stats['most_popular_day']} is the most requested day")
    
    for insight in insights:
        st.info(insight)

else:
    st.info("No historical data available. Allocations will appear here once the system has been used.")
