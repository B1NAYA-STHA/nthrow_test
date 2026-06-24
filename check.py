import os
from dotenv import load_dotenv
from nthrow.utils import create_db_connection

load_dotenv()
conn = create_db_connection(
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "password"),
    database=os.getenv("DB", "warehouse"),
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5433")
)

with conn.cursor() as cur:
    # Helper to check if a table exists
    def table_exists(name):
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);", (name,))
        return cur.fetchone()[0]

    print("\n")
    if table_exists('gis_example'):
        print("TABLE: gis_example (ALL ROWS)")

        # Show tracking state
        cur.execute("""
            SELECT state FROM gis_example WHERE list = true;
        """)
        tracking = cur.fetchone()
        if tracking:
            pagi = (tracking['state'] or {}).get('pagination', {})
            print(f"  Pagination cursor to: {pagi.get('to')}  |  from: {pagi.get('from')}")

        # Fetch ALL district records, sorted by districtCd
        cur.execute("""
            SELECT
                data->>'districtCd'         AS district_cd,
                data->>'provinceEng'        AS province,
                data->>'provinceNep'        AS province_nep,
                data->>'birthmale'          AS birth_m,
                data->>'birthfemale'        AS birth_f,
                data->>'birthtotal'         AS births,
                data->>'deathmale'          AS death_m,
                data->>'deathfemale'        AS death_f,
                data->>'deathtotal'         AS deaths,
                data->>'marriagetotal'      AS marriages,
                data->>'divorcetotal'       AS divorces,
                data->>'migrantTototal'     AS migrant_to,
                data->>'migrantFromtotal'   AS migrant_from,
                data->>'migranttotal'       AS migrants,
                data->>'totalregistration'  AS total_reg
            FROM gis_example
            WHERE list IS NOT TRUE AND partial IS NOT TRUE
            ORDER BY data->>'districtCd';
        """)
        features = cur.fetchall()

        if features:
            # Print header row
            print(f"\n  Total records in DB: {len(features)}\n")
            header = (
                f"  {'CD':<5} {'Province':<20} {'District':<18} "
                f"{'Births':>8} {'Deaths':>8} {'Marriages':>10} "
                f"{'Divorces':>9} {'Migrants':>9} {'Total Reg':>11}"
            )
            print(header)

            for row in features:
                print(
                    f"  {row['district_cd'] or '-':<5} "
                    f"{(row['province'] or '-'):<20} "
                    f"{(row['province_nep'] or '-'):<18} "
                    f"{row['births']:>8} "
                    f"{row['deaths']:>8} "
                    f"{row['marriages']:>10} "
                    f"{row['divorces']:>9} "
                    f"{row['migrants']:>9} "
                    f"{row['total_reg']:>11}"
                )
        else:
            print("  No GIS records found in database.")
    else:
        print("Table 'gis_example' does not exist.")

conn.close()
