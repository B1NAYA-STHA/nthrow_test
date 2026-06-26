"""
GIS extractor for Nepal municipality monthly sweeps.

Behavior:
- Uses `searchType=W`
- Loads municipality codes from `codes.csv`
- Processes all 753 municipalities for one Nepali month per run.
- Every run processes exactly ONE month.
- After finishing a month, if the next month is also completed, it schedules the next run in 20 minutes.
- When it reaches the current (uncompleted) Nepali month, it parks and polls daily (every 24 hours)
  using the interval settings.
"""

from __future__ import annotations

import asyncio
import datetime
import os

import nepali_datetime
from dotenv import load_dotenv

from nthrow.Store import Store
from nthrow.source import SimpleSource
from nthrow.utils import create_db_connection, create_store

from gis_municipality_codes import load_municipality_codes


def build_nepali_months(start_year: int = 2078) -> list[str]:
    """Return month keys in Nepali YYYY-MM format dynamically up to the current month."""
    today = nepali_datetime.date.today()
    end_year = today.year
    end_month = today.month

    months: list[str] = []
    for year in range(start_year, end_year + 1):
        max_month = end_month if year == end_year else 12
        for month in range(1, max_month + 1):
            months.append(f"{year}-{month:02d}")
    return months


def month_bounds(month_key: str) -> tuple[str, str]:
    """Return Nepali month boundaries as YYYY-MM-01 .. YYYY-MM-32."""
    year, month = month_key.split("-")
    return f"{year}-{month}-01", f"{year}-{month}-32"


class GisMunicipalityExtractor(SimpleSource):
    """Scrape GIS data month-by-month across all municipalities."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_cache = False
        self.municipality_codes: list[dict[str, str]] = []
        self.month_keys: list[str] = []
        self._schedule_loaded = False

    def default_list_state(self):
        return {
            "pagination": {
                "from": None,
                "to": {
                    "month_index": 0,
                    "municipality_index": 0,
                },
            },
        }

    def _has_more_pages(self, row, types=["from", "to"]):
        if "to" in types:
            self.load_schedule()
            pagi = (row.get("state") or {}).get("pagination") or {}
            to_val = pagi.get("to")
            if to_val is None:
                return False
            if to_val == "":
                return True
            if isinstance(to_val, dict):
                month_index = to_val.get("month_index", 0)
                if month_index >= len(self.month_keys) - 1:
                    return False
            return True
        return False

    def load_schedule(self):
        if self._schedule_loaded:
            return

        self.municipality_codes = load_municipality_codes("codes.csv")
        self.month_keys = build_nepali_months(2078)
        self._schedule_loaded = True

        print(f"* Loaded {len(self.municipality_codes)} municipality codes from codes.csv")
        print(f"* Loaded {len(self.month_keys)} Nepali months (2078-01 through {self.month_keys[-1]})")

    def _current_plan(self, row):
        self.load_schedule()
        cursor = (row.get("state") or {}).get("pagination", {}).get("to") or {}
        month_index = int(cursor.get("month_index", 0))
        municipality_index = int(cursor.get("municipality_index", 0))

        if month_index >= len(self.month_keys):
            return None
        if municipality_index >= len(self.municipality_codes):
            return None

        month_key = self.month_keys[month_index]
        municipality = self.municipality_codes[municipality_index]
        date_from, date_to = month_bounds(month_key)
        return {
            "month_index": month_index,
            "month_key": month_key,
            "municipality_index": municipality_index,
            "municipality_code": municipality["code"],
            "municipality_name": (
                municipality.get("name_en")
                or municipality.get("name_np")
                or municipality.get("district")
                or municipality["code"]
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

    def _next_plan(self, plan):
        municipality_index = plan["municipality_index"] + 1
        month_index = plan["month_index"]

        if municipality_index >= len(self.municipality_codes):
            municipality_index = 0
            month_index += 1

        if month_index >= len(self.month_keys):
            return None

        next_to = {
            "month_index": month_index,
            "municipality_index": municipality_index,
        }

        return next_to

    def make_url(self, row, _type):
        plan = self._current_plan(row)
        if not plan:
            return None, None

        url = (
            "https://gis.donidcr.gov.np:3001/api/gis"
            f"?searchType=W&searchValue={plan['municipality_code']}"
            f"&dateFrom={plan['date_from']}&dateTo={plan['date_to']}"
        )
        return url, plan

    async def fetch_rows(self, row, _type="to"):
        try:
            self.load_schedule()
            plan = self._current_plan(row)
            if not plan:
                return {"rows": [], "state": {"pagination": {"to": None, "from": None}}}

            # If we reached the current month (or beyond), park the cursor at the current month
            # and do not fetch anything.
            if plan["month_index"] >= len(self.month_keys) - 1:
                parked_to = {
                    "month_index": plan["month_index"],
                    "municipality_index": 0,
                }
                return {
                    "rows": [],
                    "state": {"pagination": {"to": parked_to, "from": None}},
                }

            url, plan = self.make_url(row, _type)
            res = await self.http_get(url, verify=False)
            if res.status_code != 200:
                self.logger.error(f"Non-200 HTTP response: {res.status_code} for {url}")
                return self.make_error("HTTP", res.status_code, url)

            payload = res.json()
            if not isinstance(payload, list):
                payload = payload.get("data", []) if isinstance(payload, dict) else []

            rows = []
            for index, item in enumerate(payload):
                item_key = item.get("districtCd") or item.get("id") or item.get("code") or index
                item_uri = (
                    f"https://gis.donidcr.gov.np:3001/api/gis"
                    f"/{plan['municipality_code']}/{plan['month_key']}/{item_key}"
                )
                rows.append(
                    self.make_a_row(
                        row["uri"],
                        self.mini_uri(item_uri, keep_fragments=True),
                        {
                            **item,
                            "municipality_code": plan["municipality_code"],
                            "municipality_name": plan["municipality_name"],
                            "month_key": plan["month_key"],
                            "date_from": plan["date_from"],
                            "date_to": plan["date_to"],
                        },
                    )
                )

            next_plan = self._next_plan(plan)
            return {
                "rows": self.clamp_rows_length(rows),
                "state": {"pagination": {"to": next_plan, "from": None}},
            }

        except Exception as exc:
            self.logger.exception(exc)
            return self.make_error("Exception", type(exc).__name__, str(exc))

    async def run(self):
        self.load_schedule()

        row = self.get_list_row()
        plan = self._current_plan(row)
        if not plan:
            return

        current_month_index = plan["month_index"]

        # If at the current month, let collect_rows run once to write the next daily check schedule
        if current_month_index >= len(self.month_keys) - 1:
            await self.collect_rows(row)
            return

        # Otherwise, process the completed month's municipalities in a loop
        while True:
            row = self.get_list_row()
            plan = self._current_plan(row)
            if not plan or plan["month_index"] != current_month_index:
                break

            result = await self.collect_rows(row)
            if result != 0:
                break

async def main():
    load_dotenv()
    creds = {
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB"],
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
    }

    table_name = "gis_extract"
    print("Connecting to PostgreSQL...")
    try:
        conn = create_db_connection(**creds)
    except Exception as exc:
        print(f"Connection failed: {exc}")
        return

    print(f"Creating table '{table_name}'...")
    create_store(conn, table_name)

    with conn.cursor() as cur:
        cur.execute("set time zone 'Asia/Kathmandu'")
        conn.commit()

    extractor = GisMunicipalityExtractor(conn, table_name)
    extractor.set_list_info("https://gis.donidcr.gov.np:3001/api/gis")

    # delay=20     : minutes to wait between MONTH runs.
    # interval=1440: minutes to wait (1 day) when parked at the current month.
    extractor.update_settings({
        "remote": {
            "refresh": {
                "interval": 1440,
                "delay": 20,
            },
        }
    })

    print("Initializing session...")
    async with await extractor.create_session() as session:
        extractor.session = session
        print("Running extractor...")
        await extractor.run()

    store = Store(conn, table_name)
    rows = store.get(extractor.uri, limit=5)
    print(f"\nSuccessfully fetched {len(rows)} records from database:")
    for index, row in enumerate(rows, 1):
        data = row["data"] or {}
        print(
            f"{index}. Municipality {data.get('municipality_code')} | "
            f"Month: {data.get('month_key')} | "
            f"Name: {data.get('municipality_name')}"
        )
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())