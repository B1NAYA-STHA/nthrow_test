"""
View data from gis_extract table
"""

import os
import csv
from datetime import datetime
from dotenv import load_dotenv
from nthrow.utils import create_db_connection

load_dotenv()

def export_to_csv(conn, table_name="gis_extract", output_file=None):
    """
    Export all records from the table to CSV file with complete schema.
    Includes all columns: id, url, uri, data, file, partial, state, 
    next_update_at, inserted_at, updated_at, list
    """
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"gis_municipality_vitals_{timestamp}.csv"
    
    try:
        with conn.cursor() as cur:
            # Fetch all records with all columns
            cur.execute(f"""
                SELECT
                    id,
                    url,
                    uri,
                    data,
                    file,
                    partial,
                    state,
                    next_update_at,
                    inserted_at,
                    updated_at,
                    list
                FROM {table_name}
                ORDER BY data->>'municipality_code', data->>'date_range';
            """)
            
            records = cur.fetchall()
            
            if not records:
                print(f"  No records to export from {table_name}")
                return None
            
            # Write to CSV with all columns
            with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'id', 'url', 'uri', 'data', 'file', 'partial', 'state', 
                    'next_update_at', 'inserted_at', 'updated_at', 'list'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for row in records:
                    writer.writerow({
                        'id': row['id'],
                        'url': row['url'],
                        'uri': row['uri'],
                        'data': str(row['data']) if row['data'] else '',  # Full JSON data
                        'file': str(row['file']) if row['file'] else '',
                        'partial': row['partial'],
                        'state': str(row['state']) if row['state'] else '',
                        'next_update_at': row['next_update_at'].isoformat() if row['next_update_at'] else '',
                        'inserted_at': row['inserted_at'].isoformat() if row['inserted_at'] else '',
                        'updated_at': row['updated_at'].isoformat() if row['updated_at'] else '',
                        'list': row['list']
                    })
            
            print(f"  * Exported {len(records)} records to: {output_file}")
            return output_file
            
    except Exception as e:
        print(f"  * Error exporting to CSV: {e}")
        return None


conn = create_db_connection(
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    database=os.environ["DB"],
    host=os.environ["DB_HOST"],
    port=os.environ["DB_PORT"]
)

with conn.cursor() as cur:
    # Helper to check if a table exists
    def table_exists(name):
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);", (name,))
        return cur.fetchone()[0]

    table_name = "gis_extract"
    print("\n")
    
    if table_exists(table_name):
        print(f"TABLE: {table_name}")
        
        # Get total record count
        cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE partial IS NOT TRUE;")
        total_records = cur.fetchone()[0]
        print(f"Total data records (partial=False): {total_records}")
        
        # Get count of lists (should be 1 or 0)
        cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE list IS TRUE;")
        list_records = cur.fetchone()[0]
        print(f"Total list records: {list_records}")

        cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE partial IS TRUE;")
        partial_records = cur.fetchone()[0]
        print(f"Total partial records: {partial_records}")

        cur.execute(f"""
            SELECT 
                MIN(data->>'month_key') as min_month,
                MAX(data->>'month_key') as max_month,
                COUNT(DISTINCT data->>'municipality_code') as distinct_municipalities
            FROM {table_name}
            WHERE data->>'month_key' IS NOT NULL;
        """)
        stats = cur.fetchone()
        print(f"Months Scraped: {stats['min_month']} to {stats['max_month']}")
        print(f"Unique Municipalities: {stats['distinct_municipalities']}")
        
        if list_records:
            cur.execute(f"SELECT state, next_update_at FROM {table_name} WHERE list IS TRUE LIMIT 1;")
            list_info = cur.fetchone()
            print(f"List row state: {list_info['state']}")
            print(f"Next update scheduled at: {list_info['next_update_at']}")
            
        print("\nExporting details to CSV...")
        export_to_csv(conn, table_name)
    else:
        print(f"Table '{table_name}' does not exist.")

conn.close()
