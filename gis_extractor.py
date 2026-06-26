"""
GIS extractor for Nepal municipality monthly sweeps.

Behavior:
- Uses `searchType=W`
- Loads municipality codes from `codes.csv`
- Processes all 753 municipalities for one Nepali month per run
- Every run processes exactly ONE month.
- After finishing a month, if the next month is also completed, it schedules the next run in 20 minutes.
- If the next month is the current (uncompleted) month, it schedules the next run for the 1st of the next
  Nepali month at a random time between 5-8 AM NPT.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import random

import nepali_datetime
from dotenv import load_dotenv

from nthrow.Store import Store
from nthrow.source import SimpleSource
from nthrow.utils import create_db_connection, create_store

from gis_municipality_codes import load_municipality_codes

NPT = datetime.timezone(datetime.timedelta(hours=5, minutes=45))


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


def compute_next_monthly_run_time() -> datetime.datetime:
    """Return the UTC datetime for the 1st of next Nepali month at a random
    time between 5:00 and 8:00 AM NPT (stored as UTC in PostgreSQL)."""
    today_np = nepali_datetime.date.today()
    if today_np.month == 12:
        next_year = today_np.year + 1
        next_month = 1
    else:
        next_year = today_np.year
        next_month = today_np.month + 1

    hour = random.randint(5, 8)
    minute = random.randint(0, 59)

    next_run_np = nepali_datetime.datetime(next_year, next_month, 1, hour, minute, 0)
    next_run_greg_naive = next_run_np.to_datetime_datetime()

    next_run_npt = next_run_greg_naive.replace(tzinfo=NPT)
    next_run_utc = next_run_npt.astimezone(datetime.timezone.utc)
    return next_run_utc


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
                "to": {"month_index": 0, "municipality_index": 0},
            },
        }

    def load_schedule(self):
        if self._schedule_loaded:
            return

        self.municipality_codes = load_municipality_codes("codes.csv")
        self.month_keys = build_nepali_months(2078)
        self._schedule_loaded = True

        print(f"* Loaded {len(self.municipality_codes)} municipality codes from codes.csv")
        print(f"* Loaded {len(self.month_keys)} Nepali months (2078-01 through {self.month_keys[-1]})")
        print(f"* Total requests: {len(self.municipality_codes) * len(self.month_keys)}")

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

        return {"month_index": month_index, "municipality_index": municipality_index}

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
            result = self.make_url(row, _type)
            if result[0] is None:
                return {"rows": [], "state": {"pagination": {"to": None, "from": None}}}

            url, plan = result
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

    def _set_next_update_at(self, next_update_at: datetime.datetime):
        """Directly write next_update_at on the list row in the database."""
        row = self.get_list_row()
        row["next_update_at"] = next_update_at
        self.insert_rows([row])

    async def run(self):

        self.load_schedule()

        row = self.get_list_row()
        start_plan = self._current_plan(row)
        if not start_plan:
            print("* All months complete. Waiting for next scheduled run.")
            return

        current_month_index = start_plan["month_index"]

        # If the cursor is already pointing to the current (uncompleted) month, we must wait.
        if current_month_index >= len(self.month_keys) - 1:
            print(
                f"* Month {self.month_keys[current_month_index]} is the current month and is not complete. "
                "Waiting for next scheduled run."
            )
            return

        print(f"* Starting run for month {self.month_keys[current_month_index]}")

        while True:
            row = self.get_list_row()
            plan = self._current_plan(row)
            if not plan or plan["month_index"] != current_month_index:
                break

            result = await self.collect_rows(row)
            if result != 0:
                print(f"* Error on municipality {plan['municipality_index']} — stopping run.")
                return

        # Fetch the row again to read the updated cursor pagination state
        row = self.get_list_row()
        next_plan = self._current_plan(row)

        if next_plan:
            next_month_index = next_plan["month_index"]
            if next_month_index < len(self.month_keys) - 1:
                next_update_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=20)
                print(
                    f"* Completed month {self.month_keys[current_month_index]}. "
                    f"Scheduling next run in 20 minutes."
                )
            else:
                next_update_at = compute_next_monthly_run_time()
                next_run_npt = next_update_at.astimezone(NPT)
                print(
                    f"* Completed month {self.month_keys[current_month_index]}. "
                    f"Next month {self.month_keys[next_month_index]} is the current month (uncompleted). "
                    f"Scheduling next run for 1st of next Nepali month at {next_run_npt.strftime('%H:%M')} NPT."
                )
        else:
            next_update_at = compute_next_monthly_run_time()
            print("* Reached the end of month keys. Scheduling next run for 1st of next month.")

        row["next_update_at"] = next_update_at
        self.insert_rows([row])


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

    # delay=20  : minutes to wait between MONTH runs.
    #             Each full month (753 municipalities) counts as one "page" from
    #             the scheduler's perspective, so delay fires once per month run.
    # interval=0: disabled — after active months complete we set next_update_at
    #             manually to the 1st of the next Nepali month at 5-8 AM NPT.
    extractor.update_settings({
        "remote": {
            "refresh": {
                "interval": 0,
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