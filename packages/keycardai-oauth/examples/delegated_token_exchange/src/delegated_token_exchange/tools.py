"""
Google Calendar Tools for MCP Server

This module contains the Google Calendar integration tools, including:
- Data models for calendar events and parameters
- Token exchange logic for Google Calendar access
- Google Calendar API interaction functions
- The main get_calendar_events tool implementation
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field
from starlette.requests import Request
import httpx

from fastmcp import Context
from keycardai.oauth import Client, extract_bearer_token, OAuthError
from keycardai.oauth.exceptions import TokenExchangeError, AuthenticationError


# Data models for Google Calendar events
class CalendarEventDateTime(BaseModel):
    """Date/time information for calendar events."""
    dateTime: Optional[str] = None
    date: Optional[str] = None


class CalendarEventAttendee(BaseModel):
    """Attendee information for calendar events."""
    email: str
    responseStatus: str = "needsAction"


class CalendarEvent(BaseModel):
    """Structured calendar event data."""
    id: str
    summary: str
    start: Optional[CalendarEventDateTime] = None
    end: Optional[CalendarEventDateTime] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[CalendarEventAttendee]] = None


class GoogleCalendarParams(BaseModel):
    """Parameters for Google Calendar API requests."""
    maxResults: int = Field(default=10, ge=1, le=50, description="Maximum number of events to return")
    timeMin: Optional[str] = Field(default=None, description="Start time filter (ISO 8601)")
    timeMax: Optional[str] = Field(default=None, description="End time filter (ISO 8601)")
    calendarId: str = Field(default="primary", description="Calendar identifier")


def get_user_token(request: Request) -> Optional[str]:
    """Extract user's bearer token from request headers.
    
    Uses KeyCard OAuth utility function for clean, reliable extraction.
    
    Args:
        request: HTTP request object
        
    Returns:
        Bearer token if found and valid, None otherwise
    """
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
        
    # Use our clean utility function instead of manual parsing
    return extract_bearer_token(authorization)


async def exchange_for_google_calendar_token(oauth_client: Client, user_token: str) -> str:
    """Exchange user token for Google Calendar access token.
    
    Args:
        oauth_client: Configured OAuth client instance
        user_token: User's bearer token from request
        
    Returns:
        Google Calendar access token
        
    Raises:
        ValueError: If token exchange fails
    """
    try:
        # 🎉 Unified OAuth client handles everything!
        token_response = await oauth_client.token_exchange(
            subject_token=user_token,
            audience="https://www.googleapis.com",
            scope="https://www.googleapis.com/auth/calendar.readonly"
        )
        
        return token_response.access_token
        
    except TokenExchangeError as e:
        raise ValueError(f"Token exchange failed: {e.error_description or e}")
    except AuthenticationError as e:
        raise ValueError(f"Authentication failed: {e}")
    except OAuthError as e:
        raise ValueError(f"OAuth service error: {e}")


async def fetch_google_calendar_events(
    access_token: str,
    calendar_id: str = "primary",
    max_results: int = 10,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    request_id: Optional[str] = None
) -> List[CalendarEvent]:
    """Fetch events from Google Calendar API.
    
    Args:
        access_token: Google Calendar access token
        calendar_id: Calendar identifier (default: "primary")
        max_results: Maximum number of events to return
        time_min: Start time filter (ISO 8601)
        time_max: End time filter (ISO 8601)
        request_id: Request ID for logging
        
    Returns:
        List of calendar events
        
    Raises:
        ValueError: If API call fails
    """
    # Build Google Calendar API URL
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    
    params = {
        "maxResults": str(max_results),
        "singleEvents": "true",
        "orderBy": "startTime"
    }
    
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    # Add request ID for tracing if provided
    if request_id:
        headers["X-Request-ID"] = request_id
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            raise ValueError(
                f"Google Calendar API error: {e.response.status_code} {e.response.reason_phrase} - {error_body}"
            )
        except httpx.RequestError as e:
            raise ValueError(f"Failed to connect to Google Calendar API: {e}")
    
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Google Calendar API response: {e}")
    
    # Process and validate event data
    events = []
    for event_data in data.get("items", []):
        try:
            # Map Google Calendar API response to our model
            event = CalendarEvent(
                id=event_data.get("id", ""),
                summary=event_data.get("summary", "Untitled Event"),
                start=CalendarEventDateTime(**event_data["start"]) if event_data.get("start") else None,
                end=CalendarEventDateTime(**event_data["end"]) if event_data.get("end") else None,
                location=event_data.get("location"),
                description=event_data.get("description"),
                attendees=[
                    CalendarEventAttendee(
                        email=attendee.get("email", ""),
                        responseStatus=attendee.get("responseStatus", "needsAction")
                    )
                    for attendee in event_data.get("attendees", [])
                ] if event_data.get("attendees") else None
            )
            events.append(event)
            
        except Exception as e:
            # Log error but continue processing other events
            print(f"Warning: Failed to process event {event_data.get('id', 'unknown')}: {e}")
            continue
    
    return events


async def get_calendar_events(
    oauth_client: Client,
    ctx: Context,
    maxResults: int = 10,
    timeMin: Optional[str] = None,
    timeMax: Optional[str] = None,
    calendarId: str = "primary"
) -> Dict[str, Any]:
    """Get Google Calendar events for the authenticated user.
    
    Uses delegated token exchange to obtain Google Calendar access on behalf
    of the authenticated user, then fetches their calendar events.
    
    Args:
        oauth_client: Configured OAuth client instance
        ctx: Request context containing user authentication
        maxResults: Maximum number of events to return (1-50, default: 10)
        timeMin: Start time filter in ISO 8601 format (default: now)
        timeMax: End time filter in ISO 8601 format (default: 7 days from now)
        calendarId: Calendar identifier (default: "primary")
        
    Returns:
        Dictionary containing calendar events and metadata
        
    Example:
        {
            "events": [
                {
                    "id": "event123",
                    "summary": "Team Meeting",
                    "start": {"dateTime": "2024-01-15T10:00:00-08:00"},
                    "end": {"dateTime": "2024-01-15T11:00:00-08:00"},
                    "location": "Conference Room A",
                    "attendees": [{"email": "user@company.com", "responseStatus": "accepted"}]
                }
            ],
            "requestId": "abc12345",
            "totalEvents": 1
        }
    """
    # Generate unique request ID for tracing
    request_id = str(uuid.uuid4())[:8]
    
    try:
        # Get the HTTP request from context
        request = ctx.get_http_request()
        
        # Extract and validate user token
        user_token = get_user_token(request)
        if not user_token:
            return {
                "error": "❌ No authentication token available. Please ensure you're properly authenticated with Google Calendar access.",
                "requestId": request_id,
                "isError": True
            }
        
        # Validate parameters
        try:
            params = GoogleCalendarParams(
                maxResults=maxResults,
                timeMin=timeMin,
                timeMax=timeMax,
                calendarId=calendarId
            )
        except Exception as e:
            return {
                "error": f"❌ Invalid parameters: {e}",
                "requestId": request_id,
                "isError": True
            }
        
        # Set default time range if not provided
        if not params.timeMin:
            params.timeMin = datetime.now().isoformat()
        if not params.timeMax:
            params.timeMax = (datetime.now() + timedelta(days=7)).isoformat()
        
        # Exchange token for Google Calendar access
        try:
            calendar_access_token = await exchange_for_google_calendar_token(oauth_client, user_token)
        except ValueError as e:
            return {
                "error": f"❌ Token exchange failed: {e}",
                "requestId": request_id,
                "isError": True
            }
        
        # Fetch calendar events
        try:
            events = await fetch_google_calendar_events(
                access_token=calendar_access_token,
                calendar_id=params.calendarId,
                max_results=params.maxResults,
                time_min=params.timeMin,
                time_max=params.timeMax,
                request_id=request_id
            )
        except ValueError as e:
            return {
                "error": f"❌ Failed to fetch calendar events: {e}",
                "requestId": request_id,
                "isError": True
            }
        
        # Return structured response
        return {
            "events": [event.dict() for event in events],
            "requestId": request_id,
            "totalEvents": len(events),
            "parameters": {
                "calendarId": params.calendarId,
                "maxResults": params.maxResults,
                "timeMin": params.timeMin,
                "timeMax": params.timeMax
            },
            "isError": False
        }
        
    except Exception as e:
        return {
            "error": f"❌ Unexpected error: {e}",
            "requestId": request_id,
            "isError": True
        }
