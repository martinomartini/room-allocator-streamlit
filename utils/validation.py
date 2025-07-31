"""
Validation utilities for the Room Allocator application.
"""
import streamlit as st
from typing import List, Set
from config import config


def validate_team_name(team_name: str) -> bool:
    """Validate team name input."""
    if not team_name or not team_name.strip():
        st.error("❌ Team Name is required.")
        return False
    return True


def validate_contact_person(contact_person: str) -> bool:
    """Validate contact person input."""
    if not contact_person or not contact_person.strip():
        st.error("❌ Contact Person is required.")
        return False
    return True


def validate_team_size(team_size: int) -> bool:
    """Validate team size input."""
    if not config.MIN_TEAM_SIZE <= team_size <= config.MAX_TEAM_SIZE:
        st.error(f"❌ Team size must be between {config.MIN_TEAM_SIZE} and {config.MAX_TEAM_SIZE}.")
        return False
    return True


def validate_day_selection(selected_days: str) -> bool:
    """Validate day selection for project teams."""
    try:
        days_set = set(selected_days.split(','))
        if days_set not in config.VALID_DAY_PAIRS:
            st.error("❌ Invalid day selection. Must select Monday & Wednesday or Tuesday & Thursday.")
            return False
        return True
    except Exception:
        st.error("❌ Invalid day selection format.")
        return False


def validate_oasis_person_name(person_name: str) -> bool:
    """Validate person name for Oasis."""
    if not person_name or not person_name.strip():
        st.error("❌ Please enter your name.")
        return False
    return True


def validate_oasis_day_selection(selected_days: List[str]) -> bool:
    """Validate day selection for Oasis."""
    if not config.MIN_OASIS_DAYS <= len(selected_days) <= config.MAX_OASIS_DAYS:
        st.error(f"❌ Select between {config.MIN_OASIS_DAYS} and {config.MAX_OASIS_DAYS} preferred days.")
        return False
    
    # Check if all selected days are valid
    invalid_days = set(selected_days) - set(config.WEEKDAYS)
    if invalid_days:
        st.error(f"❌ Invalid days selected: {', '.join(invalid_days)}")
        return False
    
    return True


def validate_admin_password(password: str) -> bool:
    """Validate admin password."""
    return password == config.ADMIN_PASSWORD


def validate_room_capacity(capacity: int) -> bool:
    """Validate room capacity."""
    if capacity <= 0:
        st.error("❌ Room capacity must be positive.")
        return False
    return True