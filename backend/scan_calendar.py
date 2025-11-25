import os, json
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from google.oauth2 import service_account
from googleapiclient.discovery import build

print('=== Calendar Event Timezone Scanner ===')

# Load calendars list
raw = os.getenv('CALENDARS_JSON', '[]')
try:
    calendars = json.loads(raw)
except Exception as e:
    print('Error parsing CALENDARS_JSON:', e)
    calendars = []

# Google API client
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
try:
    creds = service_account.Credentials.from_service_account_file(
        '/secrets/google-sa.json',
        scopes=SCOPES
    )
    svc = build('calendar', 'v3', credentials=creds, cache_discovery=False)
except Exception as e:
    print('Google client init FAILED:', e)
    raise SystemExit(1)

now = datetime.utcnow().replace(tzinfo=timezone.utc)
later = now + timedelta(hours=48)

print('Now UTC      :', now.isoformat())
print('Scan until   :', later.isoformat())
print('Total calendars:', len(calendars))
print('--------------------------------------------')

for room in calendars:
    cid = room.get('calendarId')
    name = room.get('name') or room.get('id') or cid

    print(f"\n--- Checking {name} ({cid}) ---")

    try:
        result = svc.events().list(
            calendarId=cid,
            timeMin=now.isoformat(),
            timeMax=later.isoformat(),
            singleEvents=True,
            orderBy='startTime',
            maxResults=200
        ).execute()
    except Exception as e:
        print('  ERROR fetching events:', e)
        continue

    events = result.get('items', [])
    if not events:
        print('  No events in the next 48 hours.')
        continue

    for ev in events:
        summary = ev.get('summary', '(no title)')
        start_raw = ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date')
        end_raw   = ev.get('end', {}).get('dateTime')   or ev.get('end', {}).get('date')

        # Safe parse
        try:
            start = dateparser.parse(start_raw) if start_raw else None
            end   = dateparser.parse(end_raw) if end_raw else None
        except Exception as ex:
            print('\n  PARSE ERROR:')
            print('    Event  :', summary)
            print('    Start  :', start_raw)
            print('    End    :', end_raw)
            print('    Error  :', ex)
            continue

        # Check timezone
        if start is not None and getattr(start, 'tzinfo', None) is None:
            print('\n  ⚠ NAIVE START (NO TIMEZONE):')
            print('    Event :', summary)
            print('    Raw   :', start_raw)
            print('    Parsed:', start, 'tz=', getattr(start, 'tzinfo', None))

        if end is not None and getattr(end, 'tzinfo', None) is None:
            print('\n  ⚠ NAIVE END (NO TIMEZONE):')
            print('    Event :', summary)
            print('    Raw   :', end_raw)
            print('    Parsed:', end, 'tz=', getattr(end, 'tzinfo', None))

print('\n=== Scan Complete ===')
