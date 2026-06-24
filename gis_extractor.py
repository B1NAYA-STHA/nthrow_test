"""
Testing Extractor for GIS API
Target the API that uses `searchValue` (1 to 7) 
instead of standard page numbers.
"""

import os
import asyncio
from dotenv import load_dotenv

from nthrow.utils import create_db_connection, create_store, sha1
from nthrow.source import SimpleSource
from nthrow.Store import Store

class GisApiExtractor(SimpleSource):
    """
    Scrapes features from a GIS API by incrementing the searchValue parameter.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_cache = False

    def make_url(self, row, _type):
        args = self.prepare_request_args(row, _type)
        search_value = args["cursor"] or 1
        
        url = f"https://gis.donidcr.gov.np:3001/api/gis?searchType=D&searchValue={search_value}&dateFrom=&dateTo="
        return url, search_value

    async def fetch_rows(self, row, _type="to"):
        try:
            url, search_value = self.make_url(row, _type)
            res = await self.http_get(url, verify=False)

            if res.status_code == 200:
                items = res.json()
                if not isinstance(items, list):
                    items = items.get("data", []) if isinstance(items, dict) else []
                
                db_rows = []
                for item in items:
                    # Use districtCd as the unique identifier per record
                    district_cd = item.get("districtCd") or sha1(str(item))
                    item_uri = f"https://gis.donidcr.gov.np:3001/api/gis/district/{district_cd}"
                    
                    db_rows.append(
                        self.make_a_row(
                            row["uri"], 
                            self.mini_uri(item_uri, keep_fragments=True), 
                            item 
                        )
                    )

                db_rows = self.clamp_rows_length(db_rows)

                # Set next value: Increment searchValue up to 7, then stop (None)
                next_search_value = search_value + 1 if search_value < 7 else None

                return {
                    "rows": db_rows,
                    "state": {
                        "pagination": {
                            _type: next_search_value
                        }
                    },
                }
            else:
                self.logger.error(f"Non-200 HTTP response: {res.status_code} for {url}")
                return self.make_error("HTTP", res.status_code, url)
                
        except Exception as e:
            self.logger.exception(e)
            return self.make_error("Exception", type(e), str(e))

    async def run(self):
        """
        Runs both secondary and primary collection cycles.
        """
        await self.collect_new_rows(self.get_list_row())
        await self.collect_rows(self.get_list_row())


# Main runner
async def main():
    load_dotenv()
    
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "password")
    db_name = os.getenv("DB", "warehouse")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5433")

    creds = {
        "user": db_user,
        "password": db_password,
        "database": db_name,
        "host": db_host,
        "port": db_port,
    }
    
    table_name = "gis_example"

    print("Connecting to PostgreSQL...")
    try:
        conn = create_db_connection(**creds)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Create the database table
    print(f"Creating table '{table_name}'...")
    create_store(conn, table_name)

    # Initialize Extractor
    extractor = GisApiExtractor(conn, table_name)
    
    dataset_url = "https://gis.donidcr.gov.np:3001/api/gis"
    extractor.set_list_info(dataset_url)
    
    extractor.update_settings({
        "remote": {
            "refresh": {"interval": 60}, 
        }
    })

    print("Initializing session...")
    async with await extractor.create_session() as session:
        extractor.session = session
        
        print("Running extractor...")
        await extractor.run()

    # Query back 5 sample features
    store = Store(conn, table_name)
    rows = store.get(dataset_url, limit=5)
    
    print(f"\nSuccessfully fetched {len(rows)} GIS district records from database:")
    for i, row in enumerate(rows, 1):
        d = row["data"]
        print(
            f"{i}. District {d.get('districtCd')} - {d.get('provinceEng')} | "
            f"Births: {d.get('birthtotal')} | Deaths: {d.get('deathtotal')} | "
            f"Marriages: {d.get('marriagetotal')} | Total Registrations: {d.get('totalregistration')}"
        )

    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
