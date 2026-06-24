
import os
import asyncio
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from nthrow.utils import create_db_connection, create_store, sha1, utcnow
from nthrow.source import SimpleSource
from nthrow.Store import Store

class BooksExtractor(SimpleSource):
    """
    Scrapes books from books.toscrape.com catalog pages.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_cache = False

    def make_url(self, row, _type):
        """
        Prepares the request URL using page pagination.
        """
        args = self.prepare_request_args(row, _type)
        page = args["cursor"] or 1
        # The pagination URL pattern on books.toscrape.com is catalogue/page-X.html
        return f"https://books.toscrape.com/catalogue/page-{page}.html", page

    async def fetch_rows(self, row, _type="to"):
        """
        Fetches and extracts books from a single catalog page.
        """
        try:
            url, page = self.make_url(row, _type)
            res = await self.http_get(url)

            if res.status_code == 200:
                rows = []
                content = res.text
                soup = BeautifulSoup(content, "html.parser")
                
                # Each book is contained in an <article class="product_pod">
                book_pods = soup.find_all("article", class_="product_pod")
                for e in book_pods:
                    link_el = e.find("h3").find("a")
                    title = link_el["title"]

                    relative_href = link_el["href"]

                    clean_href = relative_href.replace("../", "")
                    book_url = f"https://books.toscrape.com/catalogue/{clean_href}"
                    
                    price = e.find("p", class_="price_color").get_text()
                    availability = e.find("p", class_="availability").get_text().strip()
                    
                    rows.append({
                        "uri": book_url,
                        "title": title,
                        "price": price,
                        "availability": availability
                    })


                rows = self.clamp_rows_length(rows)
                
                db_rows = []
                for r in rows:
                    db_rows.append(
                        self.make_a_row(
                            row["uri"], 
                            self.mini_uri(r["uri"], keep_fragments=True), 
                            r 
                        )
                    )

                next_page = page + 1 if rows else None

                return {
                    "rows": db_rows,
                    "state": {
                        "pagination": {
                            _type: next_page
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
        Executes both secondary and primary page collection cycles.
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
    
    table_name = "books_example"

    print("Connecting to PostgreSQL...")
    try:
        conn = create_db_connection(**creds)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Create the database table and index structures
    print(f"Creating table '{table_name}'...")
    create_store(conn, table_name)

    # Initialize Extractor
    extractor = BooksExtractor(conn, table_name)
    dataset_url = "https://books.toscrape.com/"
    extractor.set_list_info(dataset_url)
    
    extractor.update_settings({
        "remote": {
            "refresh": {"interval": 15},
        }
    })

    print("Initializing session...")
    async with await extractor.create_session() as session:
        extractor.session = session
        
        print("Running extractor...")
        await extractor.run()

    # Read back 5 books from the database
    store = Store(conn, table_name)
    rows = store.get(dataset_url, limit=5)
    
    print("\nSuccessfully fetched 5 books from database:")
    for i, row in enumerate(rows, 1):
        data = row["data"]
        print(f"{i}. {data['title']} - {data['price']} ({data['availability']})")

    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
